const { default: makeWASocket, DisconnectReason, useMultiFileAuthState, makeCacheableSignalKeyStore, fetchLatestBaileysVersion } = require("baileys");
const { Boom } = require("@hapi/boom");
const fs = require("fs");
const path = require("path");
const pino = require("pino");
const qrcode = require("qrcode-terminal");
const https = require("https");
const http = require("http");

// ============================================================
// WhatsApp Customer Care Bridge v1
//
// Handles customer messages on the CC phone (+266 58342168):
//   1. Receives WhatsApp message from customer
//   2. Looks up customer in ACDB by phone number
//   3. AI classifies: ticket-worthy or general inquiry
//   4. If ticket-worthy: creates O&M ticket via ugridplan API
//   5. Replies with ticket number + acknowledgment
//   6. Notifies WhatsApp group: "1PWR LS - OnM Ticket Tracker"
//   7. ugridplan dispatches email to customercare.LS@1pwrafrica.com
//
// Conversation continuity:
//   - Tracks active conversations per phone (30-min window)
//   - Follow-ups append as comments to existing ticket
// ============================================================

// --- Configuration ---
var AUTH_DIR = "/home/ubuntu/whatsapp-logger/baileys_auth_cc";
var STATE_FILE = "/home/ubuntu/whatsapp-logger/cc-state.json";
var QR_FILE = "/tmp/whatsapp-cc-qr.txt";
var CONV_FILE = "/home/ubuntu/whatsapp-logger/cc-conversations.json";
var LOG_DIR = "/home/ubuntu/whatsapp-logger/cc-logs";

// APIs
var UGRIDPLAN_API = process.env.UGRIDPLAN_API || "https://dev.ugp.1pwrafrica.com/api";
var ACDB_API = process.env.ACDB_API || "http://172.31.2.39:8100";

// Auth for ugridplan (date-based password)
var UGRIDPLAN_USER = process.env.UGRIDPLAN_USER || "whatsapp-cc";

// Notification group JID - discovered after connection, persisted in state file
var TICKET_TRACKER_GROUP_NAME = "1PWR LS - OnM Ticket Tracker";
var TICKET_TRACKER_JID = process.env.TICKET_TRACKER_JID || "";   // set after first discovery

// Load persisted state (ticket tracker JID, etc.) from previous run
try {
    if (fs.existsSync(STATE_FILE)) {
        var savedState = JSON.parse(fs.readFileSync(STATE_FILE, "utf-8"));
        if (savedState.ticketTrackerJid && !TICKET_TRACKER_JID) {
            TICKET_TRACKER_JID = savedState.ticketTrackerJid;
            console.log("[STATE] Restored ticket tracker JID: " + TICKET_TRACKER_JID);
        }
    }
} catch(e) {
    console.log("[STATE] Could not load previous state:", e.message);
}

// Conversation window (ms) - follow-ups within this window append to existing ticket
var CONVERSATION_WINDOW_MS = 30 * 60 * 1000;   // 30 minutes

// AI classification via OpenClaw
var MOONSHOT_KEY = process.env.MOONSHOT_API_KEY || "sk-biRH9QEva0y9kJUoUpi7QLpTt6ZCtSUAwFMHWqbsZzKcnr3X";
var AGENT_TIMEOUT = 30;
var AGENT_EXEC_TIMEOUT = 40000;

// Lesotho site codes for concession -> site_id mapping
var CONCESSION_TO_SITE = {
    "MAK": "MAK", "Makhunoane": "MAK",
    "LEB": "LEB", "Lebakeng": "LEB",
    "MAT": "MAT", "Matsieng": "MAT",
    "SEB": "SEB", "Semonkong East B": "SEB",
    "TOS": "TOS", "Tosing": "TOS",
    "SEH": "SEH", "Sehlabathebe": "SEH",
    "TLH": "TLH", "Tlhanyaku": "TLH",
    "MAS": "MAS", "Mashai": "MAS",
    "SHG": "SHG", "Sekgutlong": "SHG",
    "RIB": "RIB", "Ribaneng": "RIB",
    "KET": "KET", "Ketane": "KET",
    "RAL": "RAL", "Ralebese": "RAL",
    "SUA": "SUA", "Sua": "SUA",
    "LSB": "LSB", "Lesotho Sandbox": "LSB",
};

// State
var sock = null;
var isReady = false;
var reconnectAttempt = 0;
var botJid = null;         // e.g. "26658342168:42@s.whatsapp.net"
var botNumber = null;      // e.g. "26658342168"
var recentReplies = {};    // phone -> timestamp, rate limit replies
var logger = pino({ level: "silent" });

// Suppress noisy Baileys credential logging to stdout
var _origStdoutWrite = process.stdout.write.bind(process.stdout);
process.stdout.write = function(chunk, encoding, callback) {
    var str = typeof chunk === "string" ? chunk : chunk.toString();
    // Skip Baileys session key dump lines (Buffer hex, chain objects, etc.)
    if (str.indexOf("<Buffer") >= 0 || str.indexOf("_chains:") >= 0 || str.indexOf("pendingPreKey:") >= 0 ||
        str.indexOf("currentRatchet:") >= 0 || str.indexOf("indexInfo:") >= 0 || str.indexOf("registrationId:") >= 0 ||
        str.indexOf("ephemeralKeyPair:") >= 0 || str.indexOf("rootKey:") >= 0 || str.indexOf("remoteIdentityKey:") >= 0) {
        if (typeof callback === "function") callback();
        return true;
    }
    return _origStdoutWrite(chunk, encoding, callback);
};
var conversations = {};     // phone -> { ticket_id, customer, last_msg, created_at }
var ugridplanToken = null;
var tokenExpiry = 0;
var agentBusy = false;
var messageQueue = [];

// Dedup
var processedMsgIds = new Set();
var MSG_DEDUP_MAX = 500;

// Ensure directories
[AUTH_DIR, LOG_DIR].forEach(function(dir) {
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
});

// ============================================================
// CONVERSATION PERSISTENCE
// ============================================================
function loadConversations() {
    if (!fs.existsSync(CONV_FILE)) return {};
    try {
        var data = JSON.parse(fs.readFileSync(CONV_FILE, "utf-8"));
        // Purge expired conversations
        var cutoff = Date.now() - CONVERSATION_WINDOW_MS * 4;   // keep 2hr history
        var kept = {};
        Object.keys(data).forEach(function(k) {
            if (data[k].last_msg > cutoff) kept[k] = data[k];
        });
        return kept;
    } catch(e) { return {}; }
}

function saveConversations() {
    try {
        fs.writeFileSync(CONV_FILE, JSON.stringify(conversations, null, 2));
    } catch(e) {
        console.error("[CONV] Save error:", e.message);
    }
}

function getActiveConversation(phone) {
    var conv = conversations[phone];
    if (!conv) return null;
    if (Date.now() - conv.last_msg > CONVERSATION_WINDOW_MS) return null;
    return conv;
}

// ============================================================
// UGRIDPLAN AUTH (date-based password)
// ============================================================
function generateDatePassword() {
    var now = new Date();
    var yyyy = now.getFullYear();
    var mm = String(now.getMonth() + 1).padStart(2, "0");
    var yyyymm = parseInt(yyyy + mm);
    var reversed = parseInt(String(yyyymm).split("").reverse().join(""));
    var result = yyyymm / reversed;

    // Extract first 4 significant digits
    var resultStr = result.toFixed(10);
    var digitsOnly = resultStr.replace(".", "").replace("-", "");
    // Strip leading zeros
    while (digitsOnly.charAt(0) === "0") digitsOnly = digitsOnly.substring(1);
    return digitsOnly.substring(0, 4);
}

function apiRequest(url, method, body) {
    return new Promise(function(resolve, reject) {
        var parsed = new URL(url);
        var options = {
            hostname: parsed.hostname,
            port: parsed.port || (parsed.protocol === "https:" ? 443 : 80),
            path: parsed.pathname + parsed.search,
            method: method || "GET",
            headers: { "Content-Type": "application/json" },
            timeout: 15000,
        };

        if (ugridplanToken && url.indexOf(UGRIDPLAN_API) === 0) {
            options.headers["Cookie"] = "access_token=" + ugridplanToken;
        }

        var transport = parsed.protocol === "https:" ? https : http;

        // For HTTPS, don't reject self-signed certs on internal APIs
        if (parsed.protocol === "https:") {
            options.rejectUnauthorized = true;
        }

        var req = transport.request(options, function(res) {
            var chunks = [];
            res.on("data", function(c) { chunks.push(c); });
            res.on("end", function() {
                var raw = Buffer.concat(chunks).toString();
                try {
                    resolve({ status: res.statusCode, data: JSON.parse(raw), headers: res.headers });
                } catch(e) {
                    resolve({ status: res.statusCode, data: raw, headers: res.headers });
                }
            });
        });

        req.on("error", function(e) { reject(e); });
        req.on("timeout", function() { req.destroy(); reject(new Error("Request timeout")); });

        if (body) req.write(JSON.stringify(body));
        req.end();
    });
}

async function ensureUgridplanAuth() {
    if (ugridplanToken && Date.now() < tokenExpiry) return;

    var password = generateDatePassword();
    console.log("[AUTH] Logging into ugridplan as " + UGRIDPLAN_USER + "...");

    try {
        var resp = await apiRequest(UGRIDPLAN_API + "/auth/login", "POST", {
            employeeNumber: UGRIDPLAN_USER,
            password: password,
        });

        if (resp.status === 200 && resp.data && resp.data.status === "ok") {
            // Extract token from set-cookie header
            var cookies = resp.headers["set-cookie"] || [];
            for (var i = 0; i < cookies.length; i++) {
                var match = cookies[i].match(/access_token=([^;]+)/);
                if (match) {
                    ugridplanToken = match[1];
                    tokenExpiry = Date.now() + 3600000;  // 1hr
                    console.log("[AUTH] OK - token acquired");
                    return;
                }
            }
            // If no cookie but response OK, try using response directly
            console.log("[AUTH] OK but no cookie - API may have auth disabled");
            ugridplanToken = "none";
            tokenExpiry = Date.now() + 3600000;
        } else {
            console.error("[AUTH] Login failed:", JSON.stringify(resp.data).slice(0, 200));
        }
    } catch(e) {
        console.error("[AUTH] Error:", e.message);
    }
}

// ============================================================
// ACDB CUSTOMER LOOKUP
// ============================================================
async function lookupCustomerByPhone(phone) {
    var normalized = phone.replace(/[^0-9]/g, "");
    // For Lesotho numbers, ensure proper format
    if (normalized.startsWith("266")) {
        // already has country code
    } else if (normalized.startsWith("0")) {
        normalized = "266" + normalized.substring(1);
    }

    try {
        var resp = await apiRequest(ACDB_API + "/customers/by-phone/" + normalized, "GET");
        if (resp.status === 200 && resp.data && resp.data.customers && resp.data.customers.length > 0) {
            console.log("[ACDB] Found customer: " + resp.data.customers[0].first_name + " " + resp.data.customers[0].last_name);
            return resp.data.customers[0];
        }
        console.log("[ACDB] No customer found for " + normalized);
        return null;
    } catch(e) {
        console.error("[ACDB] Lookup failed:", e.message);
        return null;
    }
}

// ============================================================
// TICKET CREATION
// ============================================================
async function createTicket(siteId, faultDescription, accountNumber, reportedBy, phone, classification) {
    await ensureUgridplanAuth();

    classification = classification || {};

    var equipCat = "unknown";
    if (classification.category === "meter-issue") equipCat = "meter";
    else if (classification.category === "equipment-failure") equipCat = "electrical";
    else if (classification.category === "no-power") equipCat = "electrical";
    else if (classification.category === "vegetation") equipCat = "civil";

    var ticketData = {
        site_id: siteId || "LSB",
        fault_description: faultDescription,
        reported_by: reportedBy || ("WhatsApp: " + phone),
        equipment_category: equipCat,
        ticket_type: "corrective",
        priority: classification.priority || "P3",
    };

    if (accountNumber) {
        ticketData.account_number = accountNumber;
    }

    try {
        var resp = await apiRequest(UGRIDPLAN_API + "/om/tickets", "POST", ticketData);

        if (resp.status === 200 && resp.data && resp.data.success) {
            var ticket = resp.data.ticket;
            console.log("[TICKET] Created: " + ticket.ticket_id + " site=" + siteId);
            return ticket;
        } else {
            console.error("[TICKET] Creation failed:", JSON.stringify(resp.data).slice(0, 300));
            return null;
        }
    } catch(e) {
        console.error("[TICKET] Error:", e.message);
        return null;
    }
}

async function addTicketComment(ticketId, user, text) {
    await ensureUgridplanAuth();

    try {
        var resp = await apiRequest(
            UGRIDPLAN_API + "/om/tickets/" + ticketId + "/comments",
            "POST",
            { user: user, text: text }
        );
        if (resp.status === 200) {
            console.log("[COMMENT] Added to " + ticketId);
            return true;
        }
    } catch(e) {
        console.error("[COMMENT] Error:", e.message);
    }
    return false;
}

// ============================================================
// NOTIFY WHATSAPP GROUP
// ============================================================
async function notifyTicketGroup(ticket, customerName, phone) {
    if (!isReady || !sock || !TICKET_TRACKER_JID) {
        console.log("[NOTIFY] Skipped - no group JID or not ready");
        return;
    }

    var tid = ticket.ticket_id || "unknown";
    var site = ticket.site_id || "unknown";
    var priority = ticket.priority || "unset";
    var desc = (ticket.fault_description || "").slice(0, 200);

    var text = "\uD83C\uDFAB *New O&M Ticket from WhatsApp*\n"
        + "\n"
        + "*Ticket:* " + tid + "\n"
        + "*Site:* " + site + "\n"
        + "*Priority:* " + priority + "\n"
        + "*Customer:* " + (customerName || "Unknown") + "\n"
        + "*Phone:* " + phone + "\n"
        + "*Description:* " + desc + "\n"
        + "\n"
        + "View in ugridplan: " + UGRIDPLAN_API.replace("/api", "") + "\n";

    try {
        await sock.sendMessage(TICKET_TRACKER_JID, { text: text });
        console.log("[NOTIFY] Ticket " + tid + " -> group");
    } catch(e) {
        console.error("[NOTIFY-ERR]", e.message);
    }
}

// ============================================================
// AI CLASSIFICATION (via OpenClaw)
// ============================================================
function classifyWithAI(customerInfo, messageText, conversationHistory) {
    return new Promise(function(resolve, reject) {
        var customerContext = "";
        if (customerInfo) {
            var acctDisplay = (customerInfo.account_numbers && customerInfo.account_numbers.length > 0)
                ? customerInfo.account_numbers[0]
                : (customerInfo.customer_id_legacy || "");
            customerContext = "KNOWN CUSTOMER:\n"
                + "  Name: " + customerInfo.first_name + " " + customerInfo.last_name + "\n"
                + "  Account: " + acctDisplay + "\n"
                + "  Concession: " + customerInfo.concession + "\n"
                + "  Plot: " + customerInfo.plot_number + "\n";
        } else {
            customerContext = "UNKNOWN CUSTOMER (not found in database)\n";
        }

        var historyContext = "";
        if (conversationHistory && conversationHistory.length > 0) {
            historyContext = "\nCONVERSATION HISTORY:\n"
                + conversationHistory.map(function(h) {
                    return "  [" + h.role + "] " + h.text;
                }).join("\n") + "\n";
        }

        var prompt = "[WhatsApp Customer Care - 1PWR Lesotho]\n"
            + "You are the customer care AI for 1PWR Africa, a minigrid electricity provider in Lesotho.\n"
            + "A customer has sent a message to the customer care WhatsApp number.\n"
            + "\n"
            + customerContext
            + historyContext
            + "\n"
            + "CUSTOMER MESSAGE:\n"
            + messageText + "\n"
            + "\n"
            + "INSTRUCTIONS:\n"
            + "Analyze this message and respond with a JSON object (and nothing else) with these fields:\n"
            + "{\n"
            + '  "needs_ticket": true/false,   // Does this warrant an O&M ticket?\n'
            + '  "category": "...",             // One of: no-power, equipment-failure, meter-issue, billing, installation, vegetation, vandalism, complaint, general-inquiry\n'
            + '  "priority": "P1"/"P2"/"P3"/"P4",  // P1=outage, P2=degraded, P3=non-critical, P4=scheduled\n'
            + '  "site_id": "...",              // 3-letter site code if determinable from customer data, or "UNKNOWN"\n'
            + '  "fault_summary": "...",        // 1-sentence technical summary for the ticket (English)\n'
            + '  "customer_reply": "...",       // BILINGUAL reply: Sesotho first, then English, separated by \\n\\n\n'
            + '  "ask_for_info": true/false     // If true, the reply should ask for more details before creating a ticket\n'
            + "}\n"
            + "\n"
            + "LANGUAGE RULES (CRITICAL):\n"
            + "- customer_reply MUST be bilingual: Sesotho FIRST, then English, separated by a blank line\n"
            + "- If the customer wrote in Sesotho, still reply in both languages\n"
            + "- If the customer wrote in English, still reply in both languages\n"
            + "- Keep each language version brief (1-3 sentences)\n"
            + '- Example format: "Kea leboha ka molaetsa oa hao. Re amohetse tlaleho ea hao.\\n\\nThank you for your message. We have received your report."\n'
            + "\n"
            + "CLASSIFICATION RULES:\n"
            + "- Power outage / no electricity -> needs_ticket=true, category=no-power, P1\n"
            + "- Meter not working / wrong readings -> needs_ticket=true, category=meter-issue, P2\n"
            + "- Equipment damage / sparks / smoke -> needs_ticket=true, category=equipment-failure, P1 or P2\n"
            + "- Billing dispute / payment question -> needs_ticket=false (unless escalation needed), category=billing\n"
            + "- General questions (hours, contact info) -> needs_ticket=false, category=general-inquiry\n"
            + "- Vegetation on lines -> needs_ticket=true, category=vegetation, P3\n"
            + "- If the message is vague, set ask_for_info=true and ask for details\n"
            + "- If customer is unknown, ask for their account number or village name in the reply\n"
            + "\n"
            + "Respond ONLY with the JSON object. No markdown, no explanation.\n";

        var tmpFile = "/tmp/cc-classify-" + Date.now() + ".txt";
        fs.writeFileSync(tmpFile, prompt);

        var { exec } = require("child_process");
        var cmd = 'MOONSHOT_API_KEY="' + MOONSHOT_KEY + '" openclaw agent'
            + ' --session-id customer-care'
            + ' --thinking off'
            + ' --message "$(cat ' + tmpFile + ')"'
            + ' --json --timeout ' + AGENT_TIMEOUT;

        var start = Date.now();
        exec(cmd, { maxBuffer: 2 * 1024 * 1024, timeout: AGENT_EXEC_TIMEOUT }, function(err, stdout, stderr) {
            var elapsed = ((Date.now() - start) / 1000).toFixed(1);
            try { fs.unlinkSync(tmpFile); } catch(e) {}

            if (err) {
                console.error("[AI] Failed after " + elapsed + "s:", err.message);
                return reject(err);
            }

            try {
                var result = JSON.parse(stdout);
                if (result.status === "ok" && result.result && result.result.payloads) {
                    var text = result.result.payloads
                        .map(function(p) { return p.text; })
                        .filter(Boolean)
                        .join("\n");

                    // Parse the JSON from the AI response
                    // The AI might wrap it in markdown code fences
                    var jsonStr = text.replace(/```json\s*/g, "").replace(/```\s*/g, "").trim();
                    try {
                        var classification = JSON.parse(jsonStr);
                        console.log("[AI] Classified in " + elapsed + "s: ticket=" + classification.needs_ticket + " cat=" + classification.category);
                        resolve(classification);
                    } catch(pe) {
                        console.error("[AI] JSON parse error from AI response:", text.slice(0, 200));
                        // Fallback: create a general inquiry response
                        resolve({
                            needs_ticket: false,
                            category: "general-inquiry",
                            priority: "P3",
                            site_id: "UNKNOWN",
                            fault_summary: messageText.slice(0, 100),
                            customer_reply: "Kea leboha ha u ikopanya le 1PWR. Ka kopo hlalosa bothata ba hao ka botlalo e le hore re tle re u thuse.\n\nThank you for contacting 1PWR. Could you please describe your issue in more detail so we can assist you better?",
                            ask_for_info: true
                        });
                    }
                } else {
                    reject(new Error("Empty AI response"));
                }
            } catch(pe) {
                reject(new Error("Failed to parse openclaw output"));
            }
        });
    });
}

// ============================================================
// MESSAGE HANDLER
// ============================================================
async function handleMessage(msg) {
    // --- Layer 1: Skip own messages (standard Baileys flag) ---
    if (msg.key.fromMe) return;

    // --- Layer 2: Skip status broadcasts ---
    if (msg.key.remoteJid === "status@broadcast") return;

    // --- Layer 3: Skip messages from bot's own number ---
    // Multi-device can deliver own sent msgs with fromMe=false
    var senderJid = msg.key.remoteJid || "";
    var senderNum = senderJid.split(":")[0].split("@")[0];
    if (botNumber && senderNum === botNumber) {
        return;  // own message echoed back via multi-device
    }
    // Also check participant field (for group msgs, but just in case)
    if (msg.key.participant) {
        var partNum = msg.key.participant.split(":")[0].split("@")[0];
        if (botNumber && partNum === botNumber) return;
    }

    // --- Layer 4: Dedup by message ID ---
    var msgId = msg.key.id;
    if (processedMsgIds.has(msgId)) return;
    processedMsgIds.add(msgId);
    if (processedMsgIds.size > MSG_DEDUP_MAX) {
        var arr = Array.from(processedMsgIds);
        processedMsgIds = new Set(arr.slice(arr.length - 250));
    }

    // --- Layer 5: Skip old messages (history sync) ---
    var msgTime = (msg.messageTimestamp || 0) * 1000;
    if (Date.now() - msgTime > 120000) return;

    // --- Layer 6: Skip protocol/system messages ---
    if (msg.message && (msg.message.protocolMessage || msg.message.reactionMessage
        || msg.message.senderKeyDistributionMessage)) return;

    // Extract text
    var text = "";
    if (msg.message) {
        text = msg.message.conversation
            || (msg.message.extendedTextMessage && msg.message.extendedTextMessage.text)
            || (msg.message.imageMessage && msg.message.imageMessage.caption)
            || (msg.message.videoMessage && msg.message.videoMessage.caption)
            || "";
    }

    var jid = msg.key.remoteJid;
    var isGroup = jid.endsWith("@g.us");
    var sender = msg.pushName || jid;

    // Only handle DMs (individual customer messages), ignore group messages
    if (isGroup) {
        // Discover the ticket tracker group JID
        if (!TICKET_TRACKER_JID) {
            try {
                var groupMeta = await sock.groupMetadata(jid);
                if (groupMeta.subject && groupMeta.subject.indexOf("OnM Ticket Tracker") >= 0) {
                    TICKET_TRACKER_JID = jid;
                    console.log("[GROUP] Discovered ticket tracker: " + jid + " (" + groupMeta.subject + ")");
                    saveState({ status: "connected", ticketTrackerJid: TICKET_TRACKER_JID });
                }
            } catch(e) {}
        }
        return;
    }

    // Extract phone number from JID
    var phone = jid.replace("@s.whatsapp.net", "").replace("@lid", "");
    var chatName = msg.pushName || phone;

    // --- Layer 7: Rate limit - max 1 reply per 60s to same number ---
    var now = Date.now();
    if (recentReplies[phone] && (now - recentReplies[phone]) < 60000) {
        console.log("[RATE] Skipping " + phone + " (replied " + Math.round((now - recentReplies[phone])/1000) + "s ago)");
        return;
    }

    console.log("[MSG] " + chatName + " (" + phone + "): " + (text || "<media>").slice(0, 80));

    // Log the message
    logMessage(phone, chatName, "customer", text);

    if (!text || !text.trim()) {
        // Media-only message - acknowledge but only once per window
        recentReplies[phone] = now;
        await sendReply(jid, "Kea leboha ka molaetsa oa hao. Bakeng sa thuso e potlakileng, ka kopo hlalosa bothata ba hao ka mongolo.\n\nThank you for your message. For fastest service, please describe your issue in text so we can help you right away.");
        return;
    }

    // Queue for processing
    messageQueue.push({
        id: msgId,
        jid: jid,
        phone: phone,
        chatName: chatName,
        text: text.trim(),
        timestamp: Date.now(),
    });

    // Show typing
    try { await sock.sendPresenceUpdate("composing", jid); } catch(e) {}

    processQueue();
}

// ============================================================
// QUEUE PROCESSOR
// ============================================================
async function processQueue() {
    if (agentBusy || messageQueue.length === 0) return;
    agentBusy = true;

    // Brief delay to batch rapid messages
    await sleep(2000);

    // Collect messages from same phone
    var first = messageQueue[0];
    var batch = [];
    var remaining = [];

    for (var i = 0; i < messageQueue.length; i++) {
        if (messageQueue[i].phone === first.phone) {
            batch.push(messageQueue[i]);
        } else {
            remaining.push(messageQueue[i]);
        }
    }
    messageQueue = remaining;

    var combinedText = batch.map(function(m) { return m.text; }).join("\n");
    var phone = first.phone;
    var jid = first.jid;
    var chatName = first.chatName;

    try {
        // 1. Look up customer in ACDB
        var customer = await lookupCustomerByPhone(phone);

        // 2. Check for active conversation (existing ticket)
        var activeConv = getActiveConversation(phone);

        if (activeConv && activeConv.ticket_id) {
            // Follow-up to existing ticket - add as comment
            console.log("[FOLLOWUP] " + phone + " -> ticket " + activeConv.ticket_id);

            var commentText = "WhatsApp follow-up from " + chatName + " (" + phone + "):\n" + combinedText;
            await addTicketComment(activeConv.ticket_id, "WhatsApp:" + phone, commentText);

            // Update conversation timestamp
            activeConv.last_msg = Date.now();
            if (!activeConv.history) activeConv.history = [];
            activeConv.history.push({ role: "customer", text: combinedText, time: Date.now() });
            saveConversations();

            // AI generates a contextual reply
            var followupClassification = await classifyWithAI(customer, combinedText, activeConv.history || []);
            var reply = followupClassification.customer_reply
                || "Kea leboha ka tlhahiso-leseling. Tikete ea hao " + activeConv.ticket_id + " e ntlafalitsoe ka molaetsa oa hao. Sehlopha sa rona se ntse se sebetsa ho e lokisa.\n\nThank you for the update. Your ticket " + activeConv.ticket_id + " has been updated with your message. Our team is working on it.";

            await sendReply(jid, reply);
            logMessage(phone, chatName, "bot", reply);

            activeConv.history.push({ role: "bot", text: reply, time: Date.now() });
            saveConversations();

            agentBusy = false;
            if (messageQueue.length > 0) setTimeout(processQueue, 500);
            return;
        }

        // 3. New conversation - classify with AI
        var classification = await classifyWithAI(customer, combinedText, []);

        // Determine site_id from customer data or AI classification
        var siteId = "UNKNOWN";
        if (customer && customer.concession) {
            siteId = CONCESSION_TO_SITE[customer.concession] || customer.concession;
        }
        if (classification.site_id && classification.site_id !== "UNKNOWN") {
            siteId = classification.site_id;
        }

        // 4. Handle based on classification
        if (classification.ask_for_info && !classification.needs_ticket) {
            // Need more information - don't create ticket yet
            var reply = classification.customer_reply || "Ka kopo fana ka lintlha tse ling mabapi le bothata ba hao.\n\nCould you please provide more details about your issue?";

            // If customer unknown, ask for account number
            if (!customer) {
                reply += "\n\nHape, ka kopo re tsebise nomoro ea hao ea ak'haonte ea 1PWR kapa lebitso la motse oa hao.\n\nAlso, could you please share your 1PWR account number or the name of your village so we can look up your account.";
            }

            await sendReply(jid, reply);
            logMessage(phone, chatName, "bot", reply);

            // Start conversation tracking (no ticket yet)
            conversations[phone] = {
                ticket_id: null,
                customer: customer,
                last_msg: Date.now(),
                created_at: Date.now(),
                history: [
                    { role: "customer", text: combinedText, time: Date.now() },
                    { role: "bot", text: reply, time: Date.now() },
                ],
            };
            saveConversations();

        } else if (classification.needs_ticket) {
            // Create O&M ticket
            var faultDesc = classification.fault_summary || combinedText.slice(0, 500);
            var reportedBy = customer
                ? (customer.first_name + " " + customer.last_name + " (WhatsApp)")
                : ("WhatsApp: " + chatName + " (" + phone + ")");

            var custAcct = null;
            if (customer && customer.account_numbers && customer.account_numbers.length > 0) {
                custAcct = customer.account_numbers[0];
            }
            var ticket = await createTicket(
                siteId,
                faultDesc,
                custAcct,
                reportedBy,
                phone,
                classification
            );

            if (ticket) {
                // Success - reply with ticket number (bilingual)
                var ticketReply = classification.customer_reply || "";
                if (ticketReply && ticketReply.indexOf(ticket.ticket_id) < 0) {
                    ticketReply = "Tikete ea hao *" + ticket.ticket_id + "* e thehiloe.\nYour ticket *" + ticket.ticket_id + "* has been created.\n\n" + ticketReply;
                }
                if (!ticketReply) {
                    ticketReply = "Bothata ba hao bo ngolisitsoe e le tikete *" + ticket.ticket_id + "*. "
                        + "Sehlopha sa rona sa ts'ebetso se tsebisitsoe 'me se tla latela haufinyane. "
                        + "U ka romela lintlha tse ling mona 'me li tla kenngoa tiketeng ea hao."
                        + "\n\n"
                        + "Your issue has been logged as ticket *" + ticket.ticket_id + "*. "
                        + "Our operations team has been notified and will follow up shortly. "
                        + "You can send additional details here and they will be added to your ticket.";
                }

                await sendReply(jid, ticketReply);
                logMessage(phone, chatName, "bot", ticketReply);

                // Notify group
                await notifyTicketGroup(ticket, customer ? (customer.first_name + " " + customer.last_name) : chatName, phone);

                // Track conversation
                conversations[phone] = {
                    ticket_id: ticket.ticket_id,
                    customer: customer,
                    last_msg: Date.now(),
                    created_at: Date.now(),
                    history: [
                        { role: "customer", text: combinedText, time: Date.now() },
                        { role: "bot", text: ticketReply, time: Date.now() },
                    ],
                };
                saveConversations();
            } else {
                // Ticket creation failed - still acknowledge
                var fallbackReply = "Kea leboha ka ho tlaleha bothata bona. "
                    + "Sehlopha sa rona se tsebisitsoe 'me se tla latela haufinyane. "
                    + "Re kopa tšoarelo ka phitisetso."
                    + "\n\n"
                    + "Thank you for reporting this issue. "
                    + "Our team has been notified and will follow up shortly. "
                    + "We apologize for any inconvenience.";
                await sendReply(jid, fallbackReply);
                logMessage(phone, chatName, "bot", fallbackReply);
            }

        } else {
            // General inquiry - just reply, no ticket
            var reply = classification.customer_reply || "Kea leboha ha u ikopanya le 1PWR. Re ka u thusa joang?\n\nThank you for contacting 1PWR customer care. How can we help you?";
            await sendReply(jid, reply);
            logMessage(phone, chatName, "bot", reply);

            // Track conversation
            conversations[phone] = {
                ticket_id: null,
                customer: customer,
                last_msg: Date.now(),
                created_at: Date.now(),
                history: [
                    { role: "customer", text: combinedText, time: Date.now() },
                    { role: "bot", text: reply, time: Date.now() },
                ],
            };
            saveConversations();
        }

    } catch(err) {
        console.error("[PROCESS-ERR]", err.message);
        await sendReply(jid, "Kea leboha ha u ikopanya le 1PWR. Re amohetse molaetsa oa hao 'me sehlopha sa rona se tla latela haufinyane.\n\nThank you for contacting 1PWR. We've received your message and our team will follow up shortly.");
    } finally {
        agentBusy = false;
        try { await sock.sendPresenceUpdate("paused", jid); } catch(e) {}
        if (messageQueue.length > 0) setTimeout(processQueue, 500);
    }
}

// ============================================================
// HELPERS
// ============================================================
async function sendReply(jid, text) {
    if (!isReady || !sock || !jid) return;
    try {
        var phone = jid.split("@")[0].split(":")[0];
        recentReplies[phone] = Date.now();  // rate-limit future messages
        await sock.sendMessage(jid, { text: text });
        console.log("[REPLY] -> " + phone + ": " + text.slice(0, 60));
    } catch(e) {
        console.error("[REPLY-ERR]", e.message);
    }
}

function logMessage(phone, chatName, role, text) {
    var entry = {
        time: new Date().toISOString(),
        phone: phone,
        name: chatName,
        role: role,
        text: text,
    };
    var logFile = path.join(LOG_DIR, phone + ".jsonl");
    try {
        fs.appendFileSync(logFile, JSON.stringify(entry) + "\n");
    } catch(e) {}
}

function saveState(state) {
    try { fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2)); } catch(e) {}
}

function sleep(ms) {
    return new Promise(function(r) { setTimeout(r, ms); });
}

// ============================================================
// SOCKET CONNECTION
// ============================================================
async function startSocket() {
    var { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
    var { version } = await fetchLatestBaileysVersion();

    sock = makeWASocket({
        version: version,
        auth: {
            creds: state.creds,
            keys: makeCacheableSignalKeyStore(state.keys, logger)
        },
        logger: logger,
        printQRInTerminal: false,
        generateHighQualityLinkPreview: false,
        markOnlineOnConnect: true,
        keepAliveIntervalMs: 25000
    });

    sock.ev.process(async function(events) {
        if (events["connection.update"]) {
            var update = events["connection.update"];

            if (update.qr) {
                console.log("\n[QR] Scan with Customer Care phone (+266 58342168):");
                qrcode.generate(update.qr, { small: true });
                fs.writeFileSync(QR_FILE, update.qr);
            }

            if (update.connection === "close") {
                isReady = false;
                var statusCode = (update.lastDisconnect && update.lastDisconnect.error)
                    ? new Boom(update.lastDisconnect.error).output.statusCode : 0;
                var shouldReconnect = statusCode !== DisconnectReason.loggedOut;

                console.log("[DISCONNECTED] code=" + statusCode + " reconnect=" + shouldReconnect);
                saveState({ status: "disconnected", code: statusCode, at: new Date().toISOString(), ticketTrackerJid: TICKET_TRACKER_JID || "", ticketTrackerName: TICKET_TRACKER_GROUP_NAME });

                if (shouldReconnect) {
                    reconnectAttempt++;
                    var delay = Math.min(2000 * Math.pow(2, reconnectAttempt - 1), 30000);
                    console.log("[RECONNECT] Attempt " + reconnectAttempt + " in " + (delay/1000) + "s...");
                    setTimeout(startSocket, delay);
                } else {
                    console.log("[LOGGED_OUT] Clearing auth and restarting...");
                    fs.rmSync(AUTH_DIR, { recursive: true, force: true });
                    fs.mkdirSync(AUTH_DIR, { recursive: true });
                    reconnectAttempt = 0;
                    setTimeout(startSocket, 5000);
                }
            }

            if (update.connection === "open") {
                // Capture bot's own number for self-message filtering
                if (sock.user && sock.user.id) {
                    botJid = sock.user.id;
                    botNumber = botJid.split(":")[0].split("@")[0];
                    console.log("[BOT] My JID=" + botJid + " number=" + botNumber);
                }
                console.log("[CONNECTED] Customer Care Bridge online at " + new Date().toISOString());
                isReady = true;
                reconnectAttempt = 0;
                if (fs.existsSync(QR_FILE)) fs.unlinkSync(QR_FILE);
                saveState({ status: "connected", since: new Date().toISOString(), ticketTrackerJid: TICKET_TRACKER_JID || "", ticketTrackerName: TICKET_TRACKER_GROUP_NAME });

                // Try to discover the ticket tracker group
                if (!TICKET_TRACKER_JID) {
                    setTimeout(discoverTicketTrackerGroup, 10000);
                }

                // Authenticate with ugridplan
                setTimeout(ensureUgridplanAuth, 5000);
            }
        }

        if (events["creds.update"]) {
            await saveCreds();
        }

        if (events["messages.upsert"]) {
            var upsert = events["messages.upsert"];
            for (var i = 0; i < upsert.messages.length; i++) {
                try {
                    await handleMessage(upsert.messages[i]);
                } catch(err) {
                    console.error("[MSG_ERROR]", err.message);
                }
            }
        }

        if (events["messaging-history.set"]) {
            var hist = events["messaging-history.set"];
            console.log("[HISTORY] " + hist.messages.length + " msgs synced");
        }
    });
}

// ============================================================
// GROUP DISCOVERY
// ============================================================
async function discoverTicketTrackerGroup() {
    if (!isReady || !sock || TICKET_TRACKER_JID) return;

    console.log("[DISCOVER] Looking for '" + TICKET_TRACKER_GROUP_NAME + "'...");
    try {
        var groups = await sock.groupFetchAllParticipating();
        var jids = Object.keys(groups);

        for (var i = 0; i < jids.length; i++) {
            var group = groups[jids[i]];
            if (group.subject && group.subject.indexOf("OnM Ticket Tracker") >= 0) {
                TICKET_TRACKER_JID = jids[i];
                console.log("[DISCOVER] Found: " + TICKET_TRACKER_JID + " (" + group.subject + ")");
                saveState({
                    status: "connected",
                    ticketTrackerJid: TICKET_TRACKER_JID,
                    ticketTrackerName: group.subject,
                });
                return;
            }
        }
        console.log("[DISCOVER] Group not found among " + jids.length + " groups. Ensure the CC phone is in '" + TICKET_TRACKER_GROUP_NAME + "'.");
    } catch(e) {
        console.error("[DISCOVER-ERR]", e.message);
    }
}

// ============================================================
// HEALTH MONITORING
// ============================================================
var healthInterval = setInterval(function() {
    var mem = process.memoryUsage();
    var mbUsed = Math.round(mem.heapUsed / 1024 / 1024);
    var uptime = Math.round(process.uptime() / 60);
    var activeConvs = Object.keys(conversations).filter(function(k) {
        return conversations[k].last_msg > Date.now() - CONVERSATION_WINDOW_MS;
    }).length;

    console.log("[HEALTH] uptime=" + uptime + "min mem=" + mbUsed + "MB ready=" + isReady
        + " queue=" + messageQueue.length + " busy=" + agentBusy
        + " convos=" + activeConvs + " tracker=" + (TICKET_TRACKER_JID ? "yes" : "no"));
}, 300000);

// ============================================================
// START
// ============================================================
conversations = loadConversations();

console.log("=== WhatsApp Customer Care Bridge v1 ===");
console.log("CC Phone: +266 58342168");
console.log("ugridplan API: " + UGRIDPLAN_API);
console.log("ACDB API: " + ACDB_API);
console.log("Ticket tracker group: " + (TICKET_TRACKER_JID || "(will discover)"));
console.log("Auth dir: " + AUTH_DIR);
console.log("Conversation window: " + (CONVERSATION_WINDOW_MS / 60000) + " min");
console.log("Active conversations loaded: " + Object.keys(conversations).length);
console.log("");

startSocket();

function shutdownState(status, extra) {
    var state = {
        status: status,
        at: new Date().toISOString(),
        ticketTrackerJid: TICKET_TRACKER_JID || "",
        ticketTrackerName: TICKET_TRACKER_GROUP_NAME
    };
    if (extra) { for (var k in extra) state[k] = extra[k]; }
    return state;
}

process.on("SIGINT", function() {
    console.log("\n[SHUTDOWN] Saving state...");
    saveConversations();
    saveState(shutdownState("stopped"));
    if (healthInterval) clearInterval(healthInterval);
    process.exit(0);
});

process.on("SIGTERM", function() {
    console.log("\n[SHUTDOWN] SIGTERM...");
    saveConversations();
    saveState(shutdownState("stopped"));
    if (healthInterval) clearInterval(healthInterval);
    process.exit(0);
});

process.on("uncaughtException", function(err) {
    console.error("[FATAL] Uncaught:", err);
    saveState(shutdownState("crashed", { error: err.message }));
});

process.on("unhandledRejection", function(reason) {
    console.error("[FATAL] Rejection:", reason);
});
