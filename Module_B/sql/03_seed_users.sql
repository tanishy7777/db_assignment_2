-- ================================================================
-- SEED USERS
-- Maps to sample Members in olympia_track:
--   MemberID 18 = Vikram Reddy  (Admin)
--   MemberID 13 = Raj Kumar     (Coach)
--   MemberID 1  = Aarav Sharma  (Player)
-- Passwords: admin123 / coach123 / player123
-- ================================================================

USE olympia_auth;

INSERT INTO users (username, password_hash, role, member_id) VALUES
(
    'vikram_admin',
    '$2b$12$bhbPO9.SFG87rECtLCCV3uXeb91Ujdmsv/rdcinl0qpUENlbThmyK',
    'Admin',
    18
),
(
    'raj_coach',
    '$2b$12$3Jyg.YKaTiiabyKkBTeVWOwXPnDok6qYVfNX5HJ2szbPkJXOILL6y',
    'Coach',
    13
),
(
    'aarav_player',
    '$2b$12$GLQVY5h7FI6.fskXwXxT9.w8AyoxhDXnAMiR5uyEx/WTBie6WxGVi',
    'Player',
    1
);
