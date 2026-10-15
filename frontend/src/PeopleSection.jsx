import { useState, useEffect } from 'react'
import { readJson } from './AttendanceUtils'
import './AttendanceForm.css' // shared look: .attendance-list, .attendance-row, .attendance-notice, .attendance-hint, .evaluate-btn
import './PeopleSection.css'

// People: add a person, set each person's department, and link a login to a person (admin).
// onPeopleChanged tells the Record attendance section to reload its list after a person is added or a link moves.
function PeopleSection({ serverUrl, onPeopleChanged }) {
    const [people, setPeople] = useState([])
    const [newName, setNewName] = useState('')
    const [addingPerson, setAddingPerson] = useState(false)
    const [personNotice, setPersonNotice] = useState(null)
    const [peopleVersion, setPeopleVersion] = useState(0) // bumped after a person is added or a link moves, to re-fetch
    const [newDepartment, setNewDepartment] = useState('')
    const [departmentNotice, setDepartmentNotice] = useState(null)
    const [departments, setDepartments] = useState([]) // from GET /api/departments
    const [defaultDepartment, setDefaultDepartment] = useState('Holding')
    const [savingDepartmentId, setSavingDepartmentId] = useState(null)
    const [users, setUsers] = useState([]) // logins, from GET /api/users (admin only)
    const [savingLinkId, setSavingLinkId] = useState(null)

    function changed() {
        setPeopleVersion((v) => v + 1)
        if (onPeopleChanged) onPeopleChanged()
    }

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
                if (!ignore) setDepartmentNotice({ type: 'error', text: 'Could not load the list of people.' })
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
            setPersonNotice({ type: 'ok', text: `Added ${data.name} to ${data.department}. They now appear under Record attendance.` })
            setNewName('')
            setNewDepartment('')
            changed()
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
            changed()
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

    return (
        <details className="people-section">
            <summary>People</summary>
            <form onSubmit={handleAddPerson} className="attendance-add-person">
                <label htmlFor="new-person-name">Add a person</label>
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
                <h3 className="attendance-departments-title">Departments and logins</h3>
                <p className="attendance-hint">
                    Link a login to a person so that user can set their own attendance each morning, until the daily cutoff.
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
                                            <option value="">No login linked</option>
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
        </details>
    )
}

export default PeopleSection