-- Migration 043: Throttle autovacuum on hourly_consumption
--
-- hourly_consumption is the largest and fastest-growing table (~17M rows,
-- 8.7 GB total). Under default settings, autovacuum generates large WAL
-- bursts that can saturate disk I/O and, if disk is near-full, cause
-- PostgreSQL to crash before recovery is possible (2026-06-13 incident).
--
-- Changes:
--   autovacuum_vacuum_cost_delay = 20ms  (default: 2ms)
--     Inserts a 20ms sleep after every cost_limit worth of I/O work,
--     slowing autovacuum by ~10x and spreading WAL over time.
--
--   autovacuum_vacuum_cost_limit = 200   (explicit, matches global default)
--     Keeps the I/O budget per work-unit unchanged; only the delay grows.
--
--   autovacuum_vacuum_scale_factor = 0.01 (default: 0.2)
--     Trigger vacuum at 1% dead-tuple ratio instead of 20%.  On a 17M-row
--     table the default means 3.4M dead tuples before vacuum runs — a huge
--     backlog that produces large WAL when it eventually fires.  1% keeps
--     vacuums smaller and more frequent.
--
-- Net effect: autovacuum runs more often but each run is smaller and
-- slower, preventing the "giant single vacuum WAL burst" failure mode.

ALTER TABLE hourly_consumption SET (
    autovacuum_vacuum_cost_delay  = 20,
    autovacuum_vacuum_cost_limit  = 200,
    autovacuum_vacuum_scale_factor = 0.01
);
