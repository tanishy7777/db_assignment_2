-- =============================================================================
-- 05_indexes.sql  —  SQL Indexes for Query Optimisation (5 indexes)
-- =============================================================================
-- Slowest endpoints measured by benchmark.py --mode before (p95 latency):
--   1. GET /admin/verify-audit           p95 17.58ms  bottleneck: Python SHA-256 loop,
--                                                     not SQL — no index can help
--   2. GET /api/members/{id} [admin]     p95  7.40ms  portfolio: sub-queries on TeamMember
--   3. GET /api/performance-logs [admin] p95  7.36ms  EXPLAIN: type=ALL + Using filesort
--   4. GET /api/members/{id} [coach]     p95  7.34ms  portfolio: same as #2
--   5. GET /api/equipment [admin]        p95  7.29ms  EXPLAIN: join + filesort on IssueDate
--
-- EXPLAIN also revealed filesort issues in events and performance-logs (all roles)
-- that are addressed by the indexes below.
--
-- Indexes that were considered and rejected:
--   audit_log(log_id, …)  — log_id IS the PRIMARY KEY (clustered index); duplicate
--   EquipmentIssue(IssueDate DESC) alone — ignored by optimizer because the query
--     enters EquipmentIssue via the EquipmentID JOIN key; IssueDate must be the
--     second column of a composite starting with EquipmentID to be usable.
-- =============================================================================

USE olympia_track;

-- -----------------------------------------------------------------------------
-- INDEX 1: PerformanceLog — eliminate full table scan + filesort
--
-- Affected endpoints:
--   GET /api/performance-logs [admin]  (type=ALL, Using filesort on RecordDate)
--   GET /api/performance-logs [coach]  (subquery materialise → filesort)
--   GET /api/performance-logs [player] (type=ref by MemberID, filesort)
--   GET /api/members/{id} portfolio    (same query, inner)
--
-- Query pattern:  ORDER BY pl.RecordDate DESC
-- Before EXPLAIN: table=pl | type=ALL  | key=None     | rows=20 | Using filesort
-- Expected after: table=pl | type=index| key=(this)   | rows=20 | Backward index scan
-- -----------------------------------------------------------------------------
CREATE INDEX idx_perf_recdate
    ON PerformanceLog(RecordDate DESC);

-- -----------------------------------------------------------------------------
-- INDEX 2: PerformanceLog — composite for MemberID filter + RecordDate sort
--
-- Affected endpoints:
--   GET /api/performance-logs [player]  (WHERE MemberID=? ORDER BY RecordDate DESC)
--   GET /api/members/{id} portfolio     (same query)
--
-- Query pattern:  WHERE MemberID = ? ORDER BY RecordDate DESC
-- Before EXPLAIN: table=pl | type=ref | key=MemberID | Using filesort
-- Expected after: type=ref | key=(this) | no filesort  (composite covers both)
-- -----------------------------------------------------------------------------
CREATE INDEX idx_perf_memberid_recdate
    ON PerformanceLog(MemberID, RecordDate DESC);

-- -----------------------------------------------------------------------------
-- INDEX 3: Event — composite to cover SportID JOIN + EventDate ORDER BY
--
-- Affected endpoint:
--   GET /api/events [admin]
--
-- Query pattern:  JOIN Sport ON e.SportID = s.SportID  ORDER BY e.EventDate DESC
-- Before EXPLAIN: type=ref key=SportID rows=2 | (separate filesort step shown at
--                 Sport level as Using temporary; Using filesort)
-- Expected after: composite allows index range scan in EventDate order →
--                 eliminates Using temporary; Using filesort
-- -----------------------------------------------------------------------------
CREATE INDEX idx_event_sportid_eventdate
    ON Event(SportID, EventDate DESC);

-- -----------------------------------------------------------------------------
-- INDEX 4: Event — composite to cover TournamentID filter + EventDate ORDER BY
--
-- Affected endpoint:
--   GET /api/events?tournament_id=N
--
-- Query pattern:  WHERE e.TournamentID = ? ORDER BY e.EventDate DESC
-- Before EXPLAIN: table=e | type=ref | key=TournamentID | Using filesort
-- Expected after: composite covers both filter and sort → eliminates filesort
-- -----------------------------------------------------------------------------
CREATE INDEX idx_event_tournamentid_eventdate
    ON Event(TournamentID, EventDate DESC);

-- -----------------------------------------------------------------------------
-- INDEX 5: EquipmentIssue — composite covering JOIN key + ORDER BY
--
-- Affected endpoints:
--   GET /api/equipment/issues [admin]   (Equipment full scan → join EquipmentIssue
--                                        via EquipmentID, ORDER BY ei.IssueDate DESC)
--
-- Query pattern:
--   FROM EquipmentIssue ei
--   JOIN Equipment e ON ei.EquipmentID = e.EquipmentID   ← JOIN clause
--   ORDER BY ei.IssueDate DESC                           ← ORDER BY clause
--
-- Why (EquipmentID, IssueDate DESC) and not just IssueDate alone:
--   The optimizer drives from Equipment, then accesses EquipmentIssue through
--   the EquipmentID join key.  A single IssueDate index cannot satisfy the JOIN
--   access and so the optimizer ignores it.  The composite puts EquipmentID first
--   (matches the JOIN) and IssueDate second (satisfies ORDER BY within each
--   equipment group), allowing the optimizer to use it for both.
--
-- Before EXPLAIN:
--   table=e  | type=ALL | key=None        | rows=15 | Using temporary; Using filesort
--   table=ei | type=ref | key=EquipmentID | rows=1
-- Expected after:
--   table=e  | type=ALL | key=None        | rows=15
--   table=ei | type=ref | key=(this)      | rows=1  | no filesort on ei
-- -----------------------------------------------------------------------------
CREATE INDEX idx_eqissue_eqid_issuedate
    ON EquipmentIssue(EquipmentID, IssueDate DESC);
