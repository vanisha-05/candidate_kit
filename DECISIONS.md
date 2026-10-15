# Design Decisions

1. **Indexes.** A unique index on `employees.emp_code` enforces the employee identifier. A unique compound index on `attendance_logs (emp_code, date)` enforces one attendance record per employee per day. No punch-time index is created; queries use the natural key and open-record filters.
2. **Race conditions.** Punch-in relies on the unique attendance index as the final guard and maps duplicate-key failures to HTTP 409. Punch-out updates only an open record in one MongoDB operation. Corrections compare the record's current fields and history before updating, so a concurrent correction receives HTTP 409 rather than overwriting another audit entry.
3. **Leaderboard ties.** MongoDB assigns standard competition ranks by total late minutes. The `limit` filters ranks, not row count, so every employee tied at the cutoff is returned.
4. **Department headcount.** The summary aggregation starts with employees and looks up that month's attendance records. Employees with no matching logs remain in the pipeline and count toward headcount.
5. **Scaling.** Analytics aggregate in MongoDB rather than loading all logs into application memory. The API and integration suite have been tested against MongoDB 7. For substantially larger datasets, inspect production query plans with `explain` and add workload-specific indexes based on those plans.
