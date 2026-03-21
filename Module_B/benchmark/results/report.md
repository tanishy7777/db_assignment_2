# SQL Indexing Benchmark Report

## Response Time Comparison (ms)

| Endpoint | Before mean | After mean | Before p95 | After p95 | Δ p95 (ms) |
|----------|-------------|------------|------------|-----------|------------|
| `GET /admin/verify-audit` | 42.46 | 37.71 | 61.13 | 60.56 | +0.57 |
| `GET /api/members/1 [admin]` | 13.21 | 12.11 | 16.08 | 16.75 | -0.67 |
| `GET /api/teams/1 [admin]` | 13.46 | 15.92 | 16.03 | 19.15 | -3.12 |
| `GET /api/events [admin]` | 13.05 | 12.91 | 16.02 | 15.8 | +0.22 |
| `GET /api/members/1 [coach]` | 13.51 | 16.61 | 15.99 | 20.75 | -4.76 |

## EXPLAIN Access Plan Changes

### `GET /admin/audit-log`

**Before:**
```
  table=audit_log | type=index | possible_keys=None | key=PRIMARY | rows=100 | Extra=Backward index scan
```
**After:**
```
  table=audit_log | type=index | possible_keys=None | key=PRIMARY | rows=100 | Extra=Backward index scan
```

### `GET /api/equipment/issues [admin]`

**Before:**
```
  table=e | type=ALL | possible_keys=PRIMARY | key=None | rows=15 | Extra=Using temporary; Using filesort
  table=ei | type=ref | possible_keys=MemberID,idx_tmp_eqissue_eqid | key=idx_tmp_eqissue_eqid | rows=1 | Extra=None
  table=m | type=eq_ref | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
```
**After:**
```
  table=e | type=ALL | possible_keys=PRIMARY | key=None | rows=15 | Extra=Using temporary; Using filesort
  table=ei | type=ref | possible_keys=MemberID,idx_tmp_eqissue_eqid,idx_eqissue_eqid_issuedate | key=idx_tmp_eqissue_eqid | rows=1 | Extra=None
  table=m | type=eq_ref | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
```

### `GET /api/equipment/issues [player]`

**Before:**
```
  table=m | type=const | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=Using filesort
  table=ei | type=ref | possible_keys=MemberID,idx_tmp_eqissue_eqid | key=MemberID | rows=5 | Extra=None
  table=e | type=eq_ref | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
```
**After:**
```
  table=m | type=const | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=Using filesort
  table=ei | type=ref | possible_keys=MemberID,idx_tmp_eqissue_eqid,idx_eqissue_eqid_issuedate | key=MemberID | rows=5 | Extra=None
  table=e | type=eq_ref | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
```

### `GET /api/events [admin]`

**Before:**
```
  table=s | type=index | possible_keys=PRIMARY | key=SportName | rows=8 | Extra=Using index; Using temporary; Using filesort
  table=e | type=ref | possible_keys=VenueID,idx_tmp_event_sportid | key=idx_tmp_event_sportid | rows=2 | Extra=None
  table=v | type=eq_ref | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
  table=t | type=ALL | possible_keys=PRIMARY | key=None | rows=3 | Extra=Using where; Using join buffer (hash join)
```
**After:**
```
  table=s | type=index | possible_keys=PRIMARY | key=SportName | rows=8 | Extra=Using index; Using temporary; Using filesort
  table=e | type=ref | possible_keys=VenueID,idx_tmp_event_sportid,idx_event_sportid_eventdate | key=idx_tmp_event_sportid | rows=2 | Extra=None
  table=v | type=eq_ref | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
  table=t | type=ALL | possible_keys=PRIMARY | key=None | rows=3 | Extra=Using where; Using join buffer (hash join)
```

### `GET /api/events?tournament_id=1`

**Before:**
```
  table=e | type=ref | possible_keys=VenueID,idx_tmp_event_sportid,idx_tmp_event_tournid | key=idx_tmp_event_tournid | rows=3 | Extra=Using filesort
  table=t | type=const | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
  table=s | type=eq_ref | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
  table=v | type=eq_ref | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
```
**After:**
```
  table=e | type=ref | possible_keys=VenueID,idx_tmp_event_sportid,idx_tmp_event_tournid,idx_event_sportid_eventdate,idx_event_tournamentid_eventdate | key=idx_event_tournamentid_eventdate | rows=3 | Extra=None
  table=t | type=const | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
  table=s | type=eq_ref | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
  table=v | type=eq_ref | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
```

### `GET /api/medical-records/1 [admin]`

**Before:**
```
  table=MedicalRecord | type=ref | possible_keys=MemberID | key=MemberID | rows=2 | Extra=Using filesort
```
**After:**
```
  table=MedicalRecord | type=ref | possible_keys=MemberID | key=MemberID | rows=2 | Extra=Using filesort
```

### `GET /api/members [admin]`

**Before:**
```
  table=Member | type=index | possible_keys=None | key=PRIMARY | rows=21 | Extra=None
```
**After:**
```
  table=Member | type=index | possible_keys=None | key=PRIMARY | rows=21 | Extra=None
```

### `GET /api/members/1 [admin]`

**Before:**
```
  table=tm | type=ref | possible_keys=PRIMARY,MemberID | key=MemberID | rows=2 | Extra=None
  table=t | type=eq_ref | possible_keys=PRIMARY,SportID | key=PRIMARY | rows=1 | Extra=None
  table=s | type=eq_ref | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
```
**After:**
```
  table=tm | type=ref | possible_keys=PRIMARY,MemberID | key=MemberID | rows=2 | Extra=None
  table=t | type=eq_ref | possible_keys=PRIMARY,SportID | key=PRIMARY | rows=1 | Extra=None
  table=s | type=eq_ref | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
```

### `GET /api/performance-logs [admin]`

**Before:**
```
  table=pl | type=ALL | possible_keys=SportID,idx_tmp_perf_memberid | key=None | rows=20 | Extra=Using filesort
  table=s | type=eq_ref | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
  table=m | type=eq_ref | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
```
**After:**
```
  table=pl | type=ALL | possible_keys=SportID,idx_tmp_perf_memberid,idx_perf_memberid_recdate | key=None | rows=20 | Extra=Using filesort
  table=s | type=eq_ref | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
  table=m | type=eq_ref | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
```

### `GET /api/performance-logs [coach]`

**Before:**
```
  table=<subquery2> | type=ALL | possible_keys=None | key=None | rows=None | Extra=Using temporary; Using filesort
  table=pl | type=ref | possible_keys=SportID,idx_tmp_perf_memberid | key=idx_tmp_perf_memberid | rows=1 | Extra=None
  table=s | type=eq_ref | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
  table=m | type=eq_ref | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
  table=t | type=ref | possible_keys=PRIMARY,CoachID | key=CoachID | rows=4 | Extra=Using index
  table=tm | type=ref | possible_keys=PRIMARY,MemberID | key=PRIMARY | rows=1 | Extra=Using index
```
**After:**
```
  table=<subquery2> | type=ALL | possible_keys=None | key=None | rows=None | Extra=Using temporary; Using filesort
  table=pl | type=ref | possible_keys=SportID,idx_tmp_perf_memberid,idx_perf_memberid_recdate | key=idx_tmp_perf_memberid | rows=1 | Extra=None
  table=s | type=eq_ref | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
  table=m | type=eq_ref | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
  table=t | type=ref | possible_keys=PRIMARY,CoachID | key=CoachID | rows=4 | Extra=Using index
  table=tm | type=ref | possible_keys=PRIMARY,MemberID | key=PRIMARY | rows=1 | Extra=Using index
```

### `GET /api/performance-logs [player]`

**Before:**
```
  table=m | type=const | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=Using filesort
  table=pl | type=ref | possible_keys=SportID,idx_tmp_perf_memberid | key=idx_tmp_perf_memberid | rows=6 | Extra=None
  table=s | type=eq_ref | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
```
**After:**
```
  table=m | type=const | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
  table=pl | type=ref | possible_keys=SportID,idx_tmp_perf_memberid,idx_perf_memberid_recdate | key=idx_perf_memberid_recdate | rows=6 | Extra=None
  table=s | type=eq_ref | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
```

### `GET /api/teams [admin]`

**Before:**
```
  table=s | type=index | possible_keys=PRIMARY | key=SportName | rows=8 | Extra=Using index; Using temporary; Using filesort
  table=t | type=ref | possible_keys=SportID | key=SportID | rows=1 | Extra=None
  table=m | type=eq_ref | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
```
**After:**
```
  table=s | type=index | possible_keys=PRIMARY | key=SportName | rows=8 | Extra=Using index; Using temporary; Using filesort
  table=t | type=ref | possible_keys=SportID | key=SportID | rows=1 | Extra=None
  table=m | type=eq_ref | possible_keys=PRIMARY | key=PRIMARY | rows=1 | Extra=None
```
