import { useState, useEffect } from 'react'
import { downloadBackup } from './AttendanceUtils'
import './AttendanceForm.css'

// Codes match the backend: 0 = Out, 1 = In, 2 = Partial Day
const STATUS_OPTIONS = [
    { value: 1, label: 'In' },
    { value: 0, label: 'Out' },
    { value: 2, label: 'Partial day' },
]

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

function AttendanceForm({ serverUrl }) {
    const today = toLocalISO(new Date())
    const [date, setDate] = useState(latestWeekday)
    const [people, setPeople] = useState([])
    const [statuses, setStatuses] = useState({}) // { [personId]: 0 | 1 | 2 }
    const [loading, setLoading] = useState(true)
    const [saving, setSaving] = useState(false)
    const [notice, setNotice] = useState(null) // { type: 'ok' | 'error', text }

    // Add-person form. peopleVersion is bumped after a successful add to re-fetch the list.
    const [newName, setNewName] = useState('')
    const [addingPerson, setAddingPerson] = useState(false)
    const [personNotice, setPersonNotice] = useState(null)
    const [peopleVersion, setPeopleVersion] = useState(0)
    const [newDepartment, setNewDepartment] = useState('')
    const [departmentNotice, setDepartmentNotice] = useState(null)
    const [departments, setDepartments] = useState([]) // from GET /api/departments
    const [defaultDepartment, setDefaultDepartment] = useState('Holding')
    const [savingDepartmentId, setSavingDepartmentId] = useState(null)
    const [users, setUsers] = useState([]) // logins, from GET /api/users (admin only)
    const [savingLinkId, setSavingLinkId] = useState(null)
    const [backupBusy, setBackupBusy] = useState(false)
    const [backupNotice, setBackupNotice] = useState(null)

    const weekend = isWeekend(date)

    // People: loaded on mount and again after one is added
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

    // Logins, for linking a login to a person. Reloaded together with the people list.
    useEffect(() => {
        let ignore = false
        fetch(`${serverUrl}/api/users`, { credentials: 'include' })
            .then(readJson)
            .then((data) => {
                if (!ignore) setUsers(data)
            })
            .catch((err) => {
                console.error('Loading logins failed:', err)
                if (!ignore) setDepartmentNotice({ type: 'error', text: 'Could not load the list of logins.' })
            })
        return () => {
            ignore = true
        }
    }, [serverUrl, peopleVersion])

    // Departments: the choices come from the server so the list lives in one place
    useEffect(() => {
        let ignore = false
        fetch(`${serverUrl}/api/departments`, { credentials: 'include' })
            .then(readJson)
            .then((data) => {
                if (ignore) return
                setDepartments(data.departments)
                setDefaultDepartment(data.default)
            })
            .catch((err) => {
                console.error('Loading departments failed:', err)
                if (!ignore) setPersonNotice({ type: 'error', text: 'Could not load the list of departments.' })
            })
        return () => {
            ignore = true
        }
    }, [serverUrl])

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

    async function handleAddPerson(e) {
        e.preventDefault()
        const name = newName.trim()
        if (!name) return

        setAddingPerson(true)
        setPersonNotice(null)
        try {
            const response = await fetch(`${serverUrl}/api/table_persons`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ name, department: newDepartment || undefined }),
            })
            const data = await readJson(response)
            setPersonNotice({ type: 'ok', text: `Added ${data.name} to ${data.department}. They now appear in the list above.` })
            setNewName('')
            setNewDepartment('')
            setPeopleVersion((v) => v + 1)
        } catch (err) {
            console.error('Adding person failed:', err)
            setPersonNotice({
                type: 'error',
                text: err instanceof TypeError ? 'Could not reach the server.' : err.message,
            })
        } finally {
            setAddingPerson(false)
        }
    }

    async function handleDepartmentChange(person, department) {
        if (department === (person.department ?? defaultDepartment)) return

        setDepartmentNotice(null)
        setSavingDepartmentId(person.id)
        try {
            const response = await fetch(`${serverUrl}/api/people/${person.id}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ department }),
            })
            const data = await readJson(response)
            setPeople((prev) => prev.map((p) => (p.id === person.id ? { ...p, department: data.department } : p)))
            setDepartmentNotice({ type: 'ok', text: `${person.name} is now in ${data.department}.` })
        } catch (err) {
            console.error('Updating department failed:', err)
            setDepartmentNotice({
                type: 'error',
                text: err instanceof TypeError ? 'Could not reach the server.' : err.message,
            })
        } finally {
            setSavingDepartmentId(null)
        }
    }

    async function handleBackup() {
        setBackupBusy(true)
        setBackupNotice(null)
        try {
            const fileName = await downloadBackup(serverUrl)
            setBackupNotice({ type: 'ok', text: `Downloaded ${fileName}.` })
        } catch (err) {
            console.error('Downloading the backup failed:', err)
            setBackupNotice({
                type: 'error',
                text: err instanceof TypeError ? 'Could not reach the server.' : err.message,
            })
        } finally {
            setBackupBusy(false)
        }
    }

    async function handleLinkChange(person, value) {
        const userId = value === '' ? null : Number(value)
        if (userId === (person.user_id ?? null)) return

        setDepartmentNotice(null)
        setSavingLinkId(person.id)
        try {
            const response = await fetch(`${serverUrl}/api/people/${person.id}`, {
                method: 'PATCH',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ user_id: userId }),
            })
            await readJson(response)
            const login = users.find((u) => u.id === userId)
            setDepartmentNotice({
                type: 'ok',
                text: login ? `${login.username} is now linked to ${person.name}.` : `${person.name} no longer has a login linked.`,
            })
            // Linking moves a login off whoever had it, so reload both lists.
            setPeopleVersion((v) => v + 1)
        } catch (err) {
            console.error('Linking a login failed:', err)
            setDepartmentNotice({
                type: 'error',
                text: err instanceof TypeError ? 'Could not reach the server.' : err.message,
            })
        } finally {
            setSavingLinkId(null)
        }
    }

    const recordedCount = people.filter((p) => statuses[p.id] !== undefined).length

    return (
        <details className="attendance-section">
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
                                            {STATUS_OPTIONS.map((o) => (
                                                <label
                                                    key={o.value}
                                                    className={`attendance-option option-${o.label.toLowerCase().replace(/\s+/g, '-')}
                                                    ${statuses[p.id] === o.value ? ' is-selected' : ''}`}
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

            <form onSubmit={handleAddPerson} className="attendance-add-person">
                <label htmlFor="new-person-name" className="attendance-label">
                    Add a person to track
                </label>
                <div className="attendance-add-person-row">
                    <input
                        id="new-person-name"
                        type="text"
                        autoComplete="off"
                        placeholder="Name"
                        value={newName}
                        onChange={(e) => setNewName(e.target.value)}
                        required
                    />
                    <select
                        id="new-person-department"
                        className="attendance-select"
                        aria-label="Department"
                        value={newDepartment}
                        onChange={(e) => setNewDepartment(e.target.value)}
                    >
                        <option value="">{defaultDepartment} (default)</option>
                        {departments
                            .filter((d) => d !== defaultDepartment)
                            .map((d) => (
                                <option key={d} value={d}>
                                    {d}
                                </option>
                            ))}
                    </select>
                    <button type="submit" className="evaluate-btn" disabled={addingPerson || !newName.trim()}>
                        {addingPerson ? 'Adding…' : 'Add person'}
                    </button>
                </div>
                {personNotice && (
                    <p
                        className={`attendance-notice attendance-notice--${personNotice.type}`}
                        role={personNotice.type === 'error' ? 'alert' : 'status'}
                    >
                        {personNotice.text}
                    </p>
                )}
            </form>

            <section className="attendance-departments">
                <h3 className="attendance-departments-title">Departments and accounts</h3>
                <p className="attendance-hint">
                    Link an account to a person so that user can set their own attendance each morning before the 8:15 AM cutoff.
                </p>
                {people.length === 0 ? (
                    <p>No one to assign yet.</p>
                ) : (
                    <ul className="attendance-list">
                        {people.map((p) => {
                            const current = p.department ?? defaultDepartment
                            // A department that was later removed from the list stays visible instead of showing the wrong one.
                            const options = departments.includes(current) ? departments : [current, ...departments]
                            return (
                                <li key={p.id} className="attendance-row">
                                    <label htmlFor={`department-${p.id}`} className="attendance-name">{p.name}</label>
                                    <div className="attendance-person-controls">
                                        <select
                                            id={`department-${p.id}`}
                                            className="attendance-select"
                                            value={current}
                                            disabled={savingDepartmentId === p.id}
                                            onChange={(e) => handleDepartmentChange(p, e.target.value)}
                                        >
                                            {options.map((d) => (
                                                <option key={d} value={d}>
                                                    {d}
                                                </option>
                                            ))}
                                        </select>
                                        <select
                                            id={`login-${p.id}`}
                                            className="attendance-select"
                                            aria-label={`Login linked to ${p.name}`}
                                            value={p.user_id ?? ''}
                                            disabled={savingLinkId === p.id}
                                            onChange={(e) => handleLinkChange(p, e.target.value)}
                                        >
                                            <option value="">No account linked</option>
                                            {users
                                                .filter((u) => u.person_id == null || u.id === p.user_id)
                                                .map((u) => (
                                                    <option key={u.id} value={u.id}>
                                                        {u.username}{u.is_admin ? ' (admin)' : ''}
                                                    </option>
                                                ))}
                                        </select>
                                    </div>
                                </li>
                            )
                        })}
                    </ul>
                )}
                {departmentNotice && (
                    <p
                        className={`attendance-notice attendance-notice--${departmentNotice.type}`}
                        role={departmentNotice.type === 'error' ? 'alert' : 'status'}
                    >
                        {departmentNotice.text}
                    </p>
                )}
            </section>

            <section className="attendance-backup">
                <h3 className="attendance-departments-title">Backup</h3>
                <p className="attendance-hint">
                    Download everyone and all their attendance as a CSV. Do this before removing attendance or
                    changing the database. Logins are not included.
                </p>
                <button type="button" className="evaluate-btn" onClick={handleBackup} disabled={backupBusy} aria-busy={backupBusy}>
                    {backupBusy ? 'Preparing…' : 'Download backup (CSV)'}
                </button>
                {backupNotice && (
                    <p
                        className={`attendance-notice attendance-notice--${backupNotice.type}`}
                        role={backupNotice.type === 'error' ? 'alert' : 'status'}
                    >
                        {backupNotice.text}
                    </p>
                )}
            </section>
        </details>
    )
}

export default AttendanceForm