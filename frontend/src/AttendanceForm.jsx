import { useState, useEffect } from 'react'
import './AttendanceForm.css'
import { STATUSES } from './AttendanceUtils'

// toISOString() converts to UTC and can shift the date by a day, so build it from local parts.
function toLocalISO(d) {
    const pad = (n) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

function isWeekend(iso) {
    const day = new Date(`${iso}T00:00:00`).getDay()
    return day === 0 || day === 6
}

function latestWeekday() {
    const d = new Date()
    while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() - 1)
    return toLocalISO(d)
}

async function readJson(response) {
    const data = await response.json()
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`)
    return data
}

// peopleVersion goes up whenever the People section adds a person or changes a link, so this list reloads.
function AttendanceForm({ serverUrl, peopleVersion = 0 }) {
    const today = toLocalISO(new Date())
    const [date, setDate] = useState(latestWeekday)
    const [people, setPeople] = useState([])
    const [statuses, setStatuses] = useState({}) // { [personId]: 0 | 1 | 2 | 3}
    const [loading, setLoading] = useState(true)
    const [saving, setSaving] = useState(false)
    const [notice, setNotice] = useState(null) // { type: 'ok' | 'error', text }

    const weekend = isWeekend(date)

    // People: loaded on mount and again after one is added in the People section
    useEffect(() => {
        let ignore = false
        fetch(`${serverUrl}/api/people`, { credentials: 'include' })
            .then(readJson)
            .then((data) => {
                if (!ignore) setPeople(data)
            })
            .catch((err) => {
                console.error('Loading people failed:', err)
                if (!ignore) setNotice({ type: 'error', text: 'Could not load the list of people.' })
            })
        return () => {
            ignore = true
        }
    }, [serverUrl, peopleVersion])

    // Recorded statuses: reloaded whenever the date changes
    useEffect(() => {
        if (isWeekend(date)) {
            setStatuses({})
            setLoading(false)
            return
        }
        let ignore = false
        setLoading(true)
        setNotice(null)
        fetch(`${serverUrl}/api/attendance?date=${date}`, { credentials: 'include' })
            .then(readJson)
            .then((data) => {
                if (ignore) return
                setStatuses(Object.fromEntries(data.records.map((r) => [r.person_id, r.status])))
            })
            .catch((err) => {
                console.error('Loading attendance failed:', err)
                if (!ignore) {
                    setStatuses({})
                    setNotice({ type: 'error', text: 'Could not load attendance for this date.' })
                }
            })
            .finally(() => {
                if (!ignore) setLoading(false)
            })
        return () => {
            ignore = true
        }
    }, [serverUrl, date])

    function setStatus(personId, value) {
        setNotice(null)
        setStatuses((prev) => ({ ...prev, [personId]: value }))
    }

    function markEveryoneIn() {
        setNotice(null)
        setStatuses(Object.fromEntries(people.map((p) => [p.id, 1])))
    }

    async function handleSave(e) {
        e.preventDefault()
        // Only people with a chosen status are sent; unchosen rows stay unrecorded.
        const records = people
            .filter((p) => statuses[p.id] !== undefined)
            .map((p) => ({ person_id: p.id, status: statuses[p.id] }))
        if (records.length === 0) return

        setSaving(true)
        setNotice(null)
        try {
            const response = await fetch(`${serverUrl}/api/attendance`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ date, records }),
            })
            const data = await readJson(response)
            setNotice({ type: 'ok', text: `Saved attendance for ${data.saved} people on ${data.date}.` })
        } catch (err) {
            console.error('Saving attendance failed:', err)
            setNotice({ type: 'error', text: err.message || 'Could not reach the server.' })
        } finally {
            setSaving(false)
        }
    }

    const recordedCount = people.filter((p) => statuses[p.id] !== undefined).length
    return (
        <details name="admin-sections" className="attendance-section">
            <summary>Record attendance</summary>
            <form onSubmit={handleSave}>
                <div className="attendance-date">
                    <label htmlFor="attendance-date">Date: </label>
                    <input
                        id="attendance-date"
                        type="date"
                        value={date}
                        max={today}
                        onChange={(e) => e.target.value && setDate(e.target.value)}
                    />
                </div>

                {weekend && (
                    <p className="attendance-notice attendance-notice--error" role="alert">
                        Attendance can only be recorded for weekdays. Pick a Monday to Friday.
                    </p>
                )}
                
                {!weekend && (
                    <>
                        {loading ? (
                            <p className="message">Loading…</p>
                        ) : (
                            <ul className="attendance-list">
                                {people.map((p) => (
                                    <li key={p.id} className="attendance-row">
                                        <span className="attendance-name">{p.name}</span>
                                        <div
                                            className="attendance-options"
                                            role="radiogroup"
                                            aria-label={`Status for ${p.name}`}
                                        >
                                            {STATUSES.map((o) => (
                                                <label
                                                    key={o.value}
                                                    className={`attendance-option option-${o.cls}${statuses[p.id] === o.value ? ' is-selected' : ''}`}
                                                    title={o.hint}
                                                >
                                                    <input
                                                        type="radio"
                                                        name={`status-${p.id}`}
                                                        checked={statuses[p.id] === o.value}
                                                        onChange={() => setStatus(p.id, o.value)}
                                                    />
                                                    {o.label}
                                                </label>
                                            ))}
                                        </div>
                                    </li>
                                ))}
                            </ul>
                        )}

                        <div className="attendance-actions">
                            <button
                                type="button"
                                className="evaluate-btn"
                                onClick={markEveryoneIn}
                                disabled={loading || saving || people.length === 0}
                            >
                                Mark everyone In
                            </button>
                            <button
                                type="submit"
                                className="evaluate-btn"
                                disabled={loading || saving || recordedCount === 0}
                                aria-busy={saving}
                            >
                                {saving ? 'Saving…' : 'Save attendance'}
                            </button>
                        </div>
                    </>
                )}

                {notice && (
                    <p
                        className={`attendance-notice attendance-notice--${notice.type}`}
                        role={notice.type === 'error' ? 'alert' : 'status'}
                    >
                        {notice.text}
                    </p>
                )}
            </form>
        </details>
    )
}

export default AttendanceForm