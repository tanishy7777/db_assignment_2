
USE olympia_track;

CREATE INDEX idx_perf_recdate
    ON PerformanceLog(RecordDate DESC);

CREATE INDEX idx_perf_memberid_recdate
    ON PerformanceLog(MemberID, RecordDate DESC);

CREATE INDEX idx_event_sportid_eventdate
    ON Event(SportID, EventDate DESC);

CREATE INDEX idx_event_tournamentid_eventdate
    ON Event(TournamentID, EventDate DESC);

CREATE INDEX idx_eqissue_eqid_issuedate
    ON EquipmentIssue(EquipmentID, IssueDate DESC);
