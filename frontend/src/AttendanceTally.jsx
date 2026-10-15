import { STATUSES } from './AttendanceUtils'

// Head-count in the bottom-right corner. First line: how many are in each state.
// Second line, larger and lighter: out of how many people in total.
// Counts only; it never says who is where.
function AttendanceTally({ byStatus, notRecorded, total }) {
    const line = [
        ...STATUSES.map((s) => `${s.short} ${byStatus[s.value] ?? 0}`),
        `Not Recorded ${notRecorded}`,
    ].join(' | ')
    return (
        <div className="daily-tally" role="status" aria-label="Attendance tally">
            <div className="daily-tally-line">{line}</div>
            <div className="daily-tally-total">(Total {total})</div>
        </div>
    )
}

export default AttendanceTally