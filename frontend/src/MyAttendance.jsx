import { useCallback, useEffect, useState } from 'react'
import AttendanceTally from './AttendanceTally'
import { STATUSES, describeError, formatCutoff, readJson, shortDate, weekendMessage } from './AttendanceUtils'
import './DailyAttendance.css'

// How often the page re-reads the server, so the tally follows what the admins do during the day.
const REFRESH_MS = 30000

// What a non-admin sees on Today's Attendance: their own status (editable until the daily cutoff)
// and a names-free head-count. The server enforces the cutoff and who the person is.
function MyAttendance({ serverUrl, onSaved }) {
    const [info, setInfo] = useState(null) // GET /api/me/attendance
    const [departments, setDepartments] = useState([])
    const [department, setDepartment] = useState(null) // null until the first load picks one; '' = everyone
    const [summary, setSummary] = useState(null) // GET /api/attendance/summary
    const [loading, setLoading] = useState(true)
    const [loadError, setLoadError] = useState(null)
    const [saving, setSaving] = useState(false)
    const [notice, setNotice] = useState(null) // { type: 'ok' | 'error', text }
    const [refreshTick, setRefreshTick] = useState(0)

    const loadInfo = useCallback(async () => {
        const response = await fetch(`${serverUrl}/api/me/attendance`, { credentials: 'include' })
        const data = await readJson(response)
        setInfo(data)
        return data
    }, [serverUrl])

    // First load: my status, and the department list for the tally dropdown.
    useEffect(() => {
        let ignore = false
        async function load() {
            try {
                const [data, list] = await Promise.all([
                    fetch(`${serverUrl}/api/me/attendance`, { credentials: 'include' }).then(readJson),
                    fetch(`${serverUrl}/api/departments`, { credentials: 'include' }).then(readJson),
                ])
                if (ignore) return
                setInfo(data)
                setDepartments(list.departments)
                // Start on my own department if I have one, otherwise on main page.
                setDepartment((current) => current ?? data.person?.department ?? '')
            } catch (err) {
                console.error('Loading attendance failed:', err)
                if (!ignore) setLoadError(describeError(err))
            } finally {
                if (!ignore) setLoading(false)
            }
        }
        load()
        return () => {
            ignore = true
        }
    }, [serverUrl])

    // The tally: changes whenever the department changes, after I save, and on the periodic refresh.
    const weekend = info?.weekend
    useEffect(() => {
        if (department === null || weekend) return
        let ignore = false
        const query = department ? `?department=${encodeURIComponent(department)}` : ''
        fetch(`${serverUrl}/api/attendance/summary${query}`, { credentials: 'include' })
            .then(readJson)
            .then((data) => {
                if (!ignore) setSummary(data)
            })
            .catch((err) => console.error('Loading the tally failed:', err))
        return () => {
            ignore = true
        }
    }, [serverUrl, department, weekend, refreshTick])

    // Periodic refresh while the page is visible.
    useEffect(() => {
        const id = setInterval(() => {
            if (document.hidden) return
            setRefreshTick((t) => t + 1)
            loadInfo().catch((err) => console.error('Refreshing attendance failed:', err))
        }, REFRESH_MS)
        return () => clearInterval(id)
    }, [loadInfo])

    // When the cutoff passes while the page is open, re-read so the buttons lock by themselves.
    const secondsLeft = info?.window_open ? info.seconds_left : 0
    useEffect(() => {
        if (!secondsLeft) return
        const id = setTimeout(() => {
            loadInfo().catch((err) => console.error('Refreshing attendance failed:', err))
        }, (secondsLeft + 1) * 1000)
        return () => clearTimeout(id)
    }, [secondsLeft, loadInfo])

    async function choose(option) {
        if (!info || option.value === info.status) return
        setSaving(true)
        setNotice(null)
        try {
            const response = await fetch(`${serverUrl}/api/me/attendance`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ status: option.value }),
            })
            await readJson(response)
            await loadInfo()
            setRefreshTick((t) => t + 1)
            setNotice({ type: 'ok', text: `Saved: ${option.label}.` })
            if (onSaved) onSaved()
        } catch (err) {
            console.error('Saving my attendance failed:', err)
            setNotice({ type: 'error', text: describeError(err) })
            // The window may just have closed, or an admin changed it: show what is true now.
            loadInfo().catch(() => {})
        } finally {
            setSaving(false)
        }
    }

    const departmentChoices =
        department && !departments.includes(department) ? [department, ...departments] : departments
    const currentLabel = STATUSES.find((s) => s.value === info?.status)?.label

    return (
        <div className="daily">
            <h1 className="daily-title">Today&apos;s Attendance</h1>
            {info && <p className="daily-date">{shortDate(info.date)}</p>}

            {loading && <p className="message">Loading…</p>}
            {!loading && loadError && (
                <p className="daily-notice daily-notice--error" role="alert">
                    {loadError}
                </p>
            )}

            {!loading && info?.weekend && <p className="message">{weekendMessage(info.date)}</p>}

            {!loading && info && !info.weekend && (
                <>
                    <section className="my-card" aria-label="Your attendance">
                        {info.linked ? (
                            <>
                                <p className="my-name">
                                    {info.person.name}
                                    <span className="my-dept"> | {info.person.department}</span>
                                </p>
                                <div className="my-options" role="radiogroup" aria-label="Your status for today">
                                    {STATUSES.map((s) => (
                                        <button
                                            key={s.value}
                                            type="button"
                                            role="radio"
                                            aria-checked={info.status === s.value}
                                            className={`my-option my-option--${s.cls}${info.status === s.value ? ' is-selected' : ''}`}
                                            disabled={!info.can_edit || saving}
                                            onClick={() => choose(s)}
                                        >
                                            {s.label}
                                        </button>
                                    ))}
                                </div>
                                <p className="my-status">
                                    {currentLabel ? `Recorded as ${currentLabel}.` : 'Not recorded yet.'}
                                </p>
                                <p className="my-hint">
                                    {info.can_edit
                                        ? `You can change this until ${formatCutoff(info.cutoff)} (${info.timezone}).`
                                        : `Changes closed at ${formatCutoff(info.cutoff)} (${info.timezone}). Ask an admin to update your attendance.`}
                                </p>
                            </>
                        ) : (
                            <p className="my-hint">
                                Your account isn&apos;t linked to a person yet, so you can&apos;t set an attendance.
                                Ask an admin to link it.
                            </p>
                        )}
                    </section>

                    <div className="my-tally-picker">
                        <label htmlFor="my-department">Show head-count for</label>
                        <select
                            id="my-department"
                            className="daily-department"
                            value={department ?? ''}
                            onChange={(e) => setDepartment(e.target.value)}
                        >
                            <option value="">All departments</option>
                            {departmentChoices.map((d) => (
                                <option key={d} value={d}>
                                    {d}
                                </option>
                            ))}
                        </select>
                    </div>
                </>
            )}

            {notice && (
                <p
                    className={`daily-notice daily-notice--${notice.type}`}
                    role={notice.type === 'error' ? 'alert' : 'status'}
                >
                    {notice.text}
                </p>
            )}

            {!loading && info && !info.weekend && summary && (
                <AttendanceTally
                    byStatus={summary.by_status}
                    notRecorded={summary.not_recorded}
                    total={summary.total}
                />
            )}
        </div>
    )
}

export default MyAttendance