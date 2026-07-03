import { useEffect, useMemo, useState } from 'react';
import { useAuth } from '../contexts/AuthContext';
import { markWhatsNewSeen } from '../lib/api';
import { WHATS_NEW_FOLIO, entriesNewerThan, type WhatsNewPage } from '../whatsnew/folio';

/* ------------------------------------------------------------------ */
/*  Body renderer: plain text with paragraph + bullet support          */
/* ------------------------------------------------------------------ */

function PageBody({ body }: { body: string }) {
  const blocks = body.split(/\n{2,}/);
  return (
    <div className="space-y-3">
      {blocks.map((block, i) => {
        const lines = block.split('\n').map((l) => l.trim()).filter(Boolean);
        const bullets = lines.filter((l) => l.startsWith('- '));
        if (bullets.length === lines.length && bullets.length > 0) {
          return (
            <ul key={i} className="list-disc list-inside text-sm text-gray-700 space-y-1.5">
              {bullets.map((b, j) => <li key={j}>{b.slice(2)}</li>)}
            </ul>
          );
        }
        return (
          <p key={i} className="text-sm text-gray-700 leading-relaxed whitespace-pre-line">
            {block.trim()}
          </p>
        );
      })}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  The modal itself                                                   */
/* ------------------------------------------------------------------ */

interface Slide extends WhatsNewPage {
  entryTitle: string;
}

function WhatsNewModal({ slides, onClose }: { slides: Slide[]; onClose: () => void }) {
  const [idx, setIdx] = useState(0);
  const slide = slides[idx];
  const isLast = idx === slides.length - 1;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      else if (e.key === 'ArrowRight' && !isLast) setIdx((i) => i + 1);
      else if (e.key === 'ArrowLeft' && idx > 0) setIdx((i) => i - 1);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [idx, isLast, onClose]);

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4 bg-black/40">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg max-h-[85vh] flex flex-col overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-100">
          <div className="flex items-center gap-2">
            <span className="text-lg">✨</span>
            <h2 className="font-semibold text-gray-800">What&apos;s new</h2>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="text-gray-400 hover:text-gray-700 rounded-md p-1 -mr-1"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Slide content */}
        <div className="px-5 py-5 overflow-y-auto flex-1">
          <div className="text-[11px] uppercase tracking-wide text-blue-600 font-semibold mb-1">
            {slide.entryTitle}
          </div>
          <h3 className="text-lg font-bold text-gray-900 mb-3">{slide.heading}</h3>
          <PageBody body={slide.body} />
        </div>

        {/* Footer / nav */}
        <div className="px-5 py-3 border-t border-gray-100 flex items-center justify-between">
          <div className="flex gap-1.5">
            {slides.map((_, i) => (
              <span
                key={i}
                className={`h-1.5 rounded-full transition-all ${i === idx ? 'w-5 bg-blue-600' : 'w-1.5 bg-gray-300'}`}
              />
            ))}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setIdx((i) => Math.max(0, i - 1))}
              disabled={idx === 0}
              className="px-3 py-1.5 text-sm rounded-lg text-gray-600 hover:bg-gray-100 disabled:opacity-40"
            >
              Back
            </button>
            {isLast ? (
              <button
                onClick={onClose}
                className="px-4 py-1.5 text-sm rounded-lg bg-blue-600 hover:bg-blue-700 text-white font-medium"
              >
                Got it
              </button>
            ) : (
              <button
                onClick={() => setIdx((i) => Math.min(slides.length - 1, i + 1))}
                className="px-4 py-1.5 text-sm rounded-lg bg-blue-600 hover:bg-blue-700 text-white font-medium"
              >
                Next
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Gate: shows the modal only when there are unseen entries           */
/* ------------------------------------------------------------------ */

export default function WhatsNewGate() {
  const { user, isEmployee } = useAuth();
  const [dismissed, setDismissed] = useState(false);

  const slides = useMemo<Slide[]>(() => {
    if (!isEmployee) return [];
    const seenAt = (user as { whats_new_seen_at?: string | null } | null)?.whats_new_seen_at ?? null;
    const unseen = entriesNewerThan(seenAt);
    return unseen.flatMap((e) => e.pages.map((p) => ({ ...p, entryTitle: e.title })));
  }, [user, isEmployee]);

  // First-time user (never acknowledged): silently initialize seen-at to now
  // so they aren't bombarded with the entire historical folio on first login.
  // The popup is for *updates since last login*; new joiners get the guide.
  useEffect(() => {
    if (!isEmployee || dismissed) return;
    const seenAt = (user as { whats_new_seen_at?: string | null } | null)?.whats_new_seen_at;
    if (seenAt == null) {
      markWhatsNewSeen().catch(() => {});
    }
  }, [user, isEmployee, dismissed]);

  if (!isEmployee || dismissed || slides.length === 0) return null;

  const handleClose = () => {
    setDismissed(true);
    markWhatsNewSeen().catch(() => {});
  };

  return <WhatsNewModal slides={slides} onClose={handleClose} />;
}

export { WHATS_NEW_FOLIO };
