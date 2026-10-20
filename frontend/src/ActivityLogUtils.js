// Helpers for the Activity log section. All of them are plain functions, so they can be tested without a browser.
import { shortDate } from './AttendanceUtils'

// Turns the filter boxes into the text that goes after "?" in /api/activity-log (and the .csv link).
// Empty boxes are left out. Returns "" when nothing is set.
export function buildLogQuery({ admin, person, action, from, to, before, limit }) {
    const params = new URLSearchParams()
    const filters = { admin, person, action, from, to, before, limit }

    for (const [name, value] of Object.entries(filters)) {
        const cleaned = String(value).trim()
        if (value == null || cleaned === '') continue
        params.set(name, cleaned)
    }
    return params.toString()
}

// What the Action filter offers, and the short name used on a grouped line. Same names as the backend's actions.
export const ACTION_LABELS = {
    attendance_set: 'Attendance set',
    attendance_removed: 'Attendance removed',
    person_added: 'Person added',
    person_renamed: 'Person renamed',
    department_changed: 'Department changed',
    person_removed: 'Person removed',
    person_restored: 'Person restored',
    login_created: 'Login created',
    login_deleted: 'Login deleted',
    login_linked: 'Login linked',
    login_unlinked: 'Login unlinked',
    password_reset: 'Password reset',
    department_request_confirmed: 'Department request approved',
    department_request_dismissed: 'Department request dismissed',
    backup_restored: 'Backup restored',
}

// '2026-10-14T11:00:00+08:00' -> 'Wed 14 Oct 11:00'. Read from the text, never through Date, so the browser's own
// timezone cannot move the time: the log is always in the office clock.
export function formatLogTime(stamp) {
    return `${shortDate(stamp.slice(0, 10))} ${stamp.slice(11, 16)}`
}

// Rows that one click wrote together (same admin, same action, same second, next to each other in the list) become
// one group, so saving the whole board reads as one line instead of thirty. The newest-first order is kept.
// Returns [{ key, rows, summary }]; a group of one row has summary null and is shown as a plain line.
export function groupEntries(entries) {
    const groups = []
    for (const entry of entries) {
        const last = groups[groups.length - 1]
        const first = last?.rows[0]
        if (first && first.admin === entry.admin && first.action === entry.action && first.logged_at === entry.logged_at) {
            last.rows.push(entry)
        } else {
            groups.push({ key: entry.seq, rows: [entry], summary: null })
        }
    }
    for (const group of groups) {
        if (group.rows.length > 1) {
            const { admin, action } = group.rows[0]
            group.summary = `${admin}: ${ACTION_LABELS[action] ?? action} ×${group.rows.length}`
        }
    }
    return groups
}
