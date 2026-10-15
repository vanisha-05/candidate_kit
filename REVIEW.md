# Code Review

The following defects were identified in the starter implementation and addressed:

| # | Area | Defect | How it appears | Fix |
|---|---|---|---|---|
| 1 | Punch-in | An unknown `emp_code` was not handled before using the employee record. | Punching in with an unregistered code caused an error instead of a not-found response. | Look up the employee first and return HTTP 404 when absent. |
| 2 | Punch-in | Epoch timestamps were interpreted in the machine's local timezone, and seconds/floats/strings were not strictly rejected. | IST dates could be wrong across environments, and invalid timestamp formats could pass through. | Validate integer epoch milliseconds, truncate to seconds, and derive the attendance day in IST, including overnight shifts. |
| 3 | Punch-in | Late calculation used `> 10` whole minutes, incorrectly excluding times just over the 10-minute grace period. | A punch at 09:40:01 for a 09:30 shift was recorded as not late. | Apply the strict 10-minute threshold in seconds and floor elapsed minutes. |
| 4 | Punch-in and employee creation | Check-then-insert duplicate checks were vulnerable to concurrent requests. | Simultaneous creates could both pass the pre-check. | Add unique indexes for `emp_code` and (`emp_code`, `date`), and translate duplicate-key failures into HTTP 409 responses. |
| 5 | Time calculations | Work-hour rounding used Python's banker's rounding; overtime did not enforce the 30-minute minimum or correctly handle overnight shifts. | Boundary values could round incorrectly or count ineligible overtime. | Use half-up rounding, apply the R3 threshold, and compute shift end on the correct local date. |
| 6 | Punch-out and corrections | The starter had no punch-out or correction flow. | Records could not be closed or corrected through the API. | Add derived-field recomputation, atomic open-record updates, validation, and append-only correction history. |
| 7 | Analytics | The starter lacked leaderboard and department-summary implementations. | Tie ranks and employees without logs were not represented correctly. | Rank via MongoDB aggregation and return all rows within the rank cutoff; start department summaries from employees and preserve unmatched employees in the lookup. |

No issue was found with storing calendar dates as `YYYY-MM-DD` strings: that matches the data model and avoids treating a calendar day as an instant. Punch events are stored as BSON datetimes and exposed as epoch milliseconds.
