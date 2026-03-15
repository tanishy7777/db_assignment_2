-- ================================================================
-- Phase 4: Tournament Registration Table
-- Tracks which teams are registered for which tournaments.
-- Teams must register for a tournament before joining its events.
-- ================================================================

USE olympia_track;

CREATE TABLE IF NOT EXISTS TournamentRegistration (
    RegID        INT NOT NULL,
    TournamentID INT NOT NULL,
    TeamID       INT NOT NULL,
    PRIMARY KEY (RegID),
    UNIQUE KEY uq_tr (TournamentID, TeamID),
    FOREIGN KEY (TournamentID) REFERENCES Tournament(TournamentID)
        ON DELETE CASCADE ON UPDATE CASCADE,
    FOREIGN KEY (TeamID) REFERENCES Team(TeamID)
        ON DELETE CASCADE ON UPDATE CASCADE
);
