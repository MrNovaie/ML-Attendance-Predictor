import { useState, useEffect, useMemo } from 'react'
import { csvCell, describeError, downloadTextFile, matchesSearch, readJson } from './AttendanceUtils'
import './AttendanceForm.css' // shared look: .attendance-list, .attendance-row, .attendance-notice, .attendance-hint, .evaluate-btn
import './AccountsSection.css'

// Accounts (admin): make logins for many people at once, and answer the department a person asked for when
// they signed up. Adding, removing and resetting the password of a single login is in the user list below (Manage users).
function AccountsSection({ serverUrl, peopleVersion = 0, onPeopleChanged }) {
    const [people, setPeople] = useState([])
    const [users, setUsers] = useState([])
    const [version, setVersion] = useState(0) // bumped after something here changes, to re-fetch
    const [loadError, setLoadError] = useState(null)

    // bulk
    const [search, setSearch] = useState('')
    const [picked, setPicked] = useState(() => new Set()) // person ids ticked
    const [creating, setCreating] = useState(false)
    const [created, setCreated] = useState(null) // [{ person, username, temporary_password }] from the last batch
    const [bulkNotice, setBulkNotice] = useState(null)

    // sign-up requests
    const [answeringId, setAnsweringId] = useState(null)
    const [requestNotice, setRequestNotice] = useState(null)

    useEffect(() => {
        let ignore = false
        Promise.all([
            fetch(`${serverUrl}/api/people`, { credentials: 'include' }).then(readJson),
            fetch(`${serverUrl}/api/users`, { credentials: 'include' }).then(readJson),
        ])
            .then(([peopleData, usersData]) => {
                if (ignore) return
                setPeople(peopleData)
                setUsers(usersData)
                setLoadError(null)
            })
            .catch((err) => {
                console.error('Loading accounts failed:', err)
                if (!ignore) setLoadError(describeError(err))
            })
        return () => {
            ignore = true
        }
    }, [serverUrl, version, peopleVersion])

    const withoutLogin = useMemo(
        () => people.filter((p) => p.user_id == null).sort((a, b) => a.name.localeCompare(b.name)),
        [people],
    )
    const shown = withoutLogin.filter((p) => matchesSearch(p.name, search))
    const personOf = useMemo(() => new Map(people.filter((p) => p.user_id != null).map((p) => [p.user_id, p])), [people])
    const requests = users.filter((u) => u.requested_department)
    const pickedCount = [...picked].filter((id) => withoutLogin.some((p) => p.id === id)).length

    function toggle(id) {
        setPicked((prev) => {
            const next = new Set(prev)
            if (next.has(id)) next.delete(id)
            else next.add(id)
            return next
        })
    }

    async function handleCreate() {
        const ids = withoutLogin.filter((p) => picked.has(p.id)).map((p) => p.id)
        if (ids.length === 0) return
        setCreating(true)
        setBulkNotice(null)
        try {
            const data = await fetch(`${serverUrl}/api/users/bulk`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ person_ids: ids }),
            }).then(readJson)
            setCreated(data.created)
            setPicked(new Set())
            setSearch('')
            setVersion((v) => v + 1)
            if (onPeopleChanged) onPeopleChanged()
        } catch (err) {
            console.error('Creating logins failed:', err)
            setBulkNotice({ type: 'error', text: describeError(err) })
        } finally {
            setCreating(false)
        }
    }

    function handleDownloadLogins() {
        const lines = [['person', 'username', 'temporary_password'], ...created.map((c) => [c.person, c.username, c.temporary_password])]
        const today = new Date().toISOString().slice(0, 10)
        downloadTextFile(`new-logins-${today}.csv`, lines.map((row) => row.map(csvCell).join(',')).join('\r\n') + '\r\n')
    }

    async function handleAnswer(user, action) {
        setAnsweringId(user.id)
        setRequestNotice(null)
        try {
            await fetch(`${serverUrl}/api/users/${user.id}/department-request`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ action }),
            }).then(readJson)
            setRequestNotice({
                type: 'ok',
                text:
                    action === 'confirm'
                        ? `${personOf.get(user.id)?.name ?? user.username} is now in ${user.requested_department}.`
                        : `Dismissed the request from ${user.username}.`,
            })
            setVersion((v) => v + 1)
            if (action === 'confirm' && onPeopleChanged) onPeopleChanged()
        } catch (err) {
            console.error('Answering the department request failed:', err)
            setRequestNotice({ type: 'error', text: describeError(err) })
        } finally {
            setAnsweringId(null)
        }
    }

    return (
        <details name="admin-sections" className="accounts-section">
            <summary>
                Accounts{requests.length > 0 && <span className="accounts-badge" aria-label={`${requests.length} sign-up requests`}>{requests.length}</span>}
            </summary>
            {loadError && <p className="attendance-notice attendance-notice--error" role="alert">{loadError}</p>}

            <section className="accounts-block">
                <h3 className="attendance-departments-title">Create logins for people</h3>
                <p className="attendance-hint">
                    Creates a login for each ticked person, with a temporary password they must change at first login.
                </p>
                {created ? (
                    <div className="accounts-created" role="status">
                        <p>
                            <strong>Created {created.length} {created.length === 1 ? 'login' : 'logins'}.</strong> The temporary passwords are
                            shown here once and cannot be looked up later. Download or print them now.
                        </p>
                        <table className="accounts-table">
                            <thead>
                                <tr><th>Person</th><th>Username</th><th>Temporary password</th></tr>
                            </thead>
                            <tbody>
                                {created.map((c) => (
                                    <tr key={c.person_id}>
                                        <td>{c.person}</td>
                                        <td>{c.username}</td>
                                        <td><code>{c.temporary_password}</code></td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                        <div className="accounts-buttons">
                            <button type="button" className="evaluate-btn" onClick={handleDownloadLogins}>Download CSV</button>
                            <button type="button" className="evaluate-btn" onClick={() => setCreated(null)}>Done, hide the passwords</button>
                        </div>
                    </div>
                ) : withoutLogin.length === 0 ? (
                    <p>Everyone already has a login.</p>
                ) : (
                    <>
                        <div className="accounts-buttons">
                            <input
                                type="search"
                                aria-label="Search people without a login"
                                placeholder="Search by name"
                                autoComplete="off"
                                value={search}
                                onChange={(e) => setSearch(e.target.value)}
                            />
                            <button
                                type="button"
                                className="evaluate-btn"
                                onClick={() => setPicked((prev) => new Set([...prev, ...shown.map((p) => p.id)]))}
                                disabled={shown.length === 0}
                            >
                                Tick all shown
                            </button>
                            <button type="button" className="evaluate-btn" onClick={() => setPicked(new Set())} disabled={picked.size === 0}>
                                Clear
                            </button>
                        </div>
                        <ul className="accounts-picklist">
                            {shown.map((p) => (
                                <li key={p.id}>
                                    <label>
                                        <input type="checkbox" checked={picked.has(p.id)} onChange={() => toggle(p.id)} />
                                        {p.name} <span className="accounts-dim">{p.department}</span>
                                    </label>
                                </li>
                            ))}
                            {shown.length === 0 && <li className="accounts-dim">No one matches.</li>}
                        </ul>
                        <button type="button" className="evaluate-btn" onClick={handleCreate} disabled={creating || pickedCount === 0}>
                            {creating ? 'Creating…' : `Create ${pickedCount} ${pickedCount === 1 ? 'login' : 'logins'}`}
                        </button>
                    </>
                )}
                {bulkNotice && (
                    <p className={`attendance-notice attendance-notice--${bulkNotice.type}`} role={bulkNotice.type === 'error' ? 'alert' : 'status'}>
                        {bulkNotice.text}
                    </p>
                )}
            </section>

            {requests.length > 0 && (
                <section className="accounts-block">
                    <h3 className="attendance-departments-title">Sign-up requests</h3>
                    <p className="attendance-hint">
                        A department someone asked for when they signed up. Confirming moves the linked person to the requested department. Link the login first under People.
                    </p>
                    <ul className="attendance-list">
                        {requests.map((u) => {
                            const person = personOf.get(u.id)
                            return (
                                <li key={u.id} className="attendance-row">
                                    <span className="attendance-name">
                                        {u.username} asked for <strong>{u.requested_department}</strong>{' '}
                                        <span className="accounts-dim">{person ? `(${person.name}, now ${person.department})` : '(not linked to a person yet)'}</span>
                                    </span>
                                    <span className="accounts-buttons">
                                        <button
                                            type="button"
                                            className="evaluate-btn"
                                            disabled={answeringId === u.id || !person}
                                            title={person ? undefined : 'Link this login to a person first'}
                                            onClick={() => handleAnswer(u, 'confirm')}
                                        >
                                            Confirm
                                        </button>
                                        <button type="button" className="evaluate-btn" disabled={answeringId === u.id} onClick={() => handleAnswer(u, 'dismiss')}>
                                            Dismiss
                                        </button>
                                    </span>
                                </li>
                            )
                        })}
                    </ul>
                    {requestNotice && (
                        <p className={`attendance-notice attendance-notice--${requestNotice.type}`} role={requestNotice.type === 'error' ? 'alert' : 'status'}>
                            {requestNotice.text}
                        </p>
                    )}
                </section>
            )}
        </details>
    )
}

export default AccountsSection