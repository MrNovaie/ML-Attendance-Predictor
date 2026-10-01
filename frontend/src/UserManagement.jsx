import { useState, useEffect, useCallback } from 'react'
import './UserManagement.css'

async function readJson(response) {
    const data = await response.json()
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`)
    return data
}

// fetch() rejects with a TypeError when the server is unreachable.
function errorText(err) {
    return err instanceof TypeError ? 'Could not reach the server.' : err.message
}

function UserManagement({ serverUrl, currentUsername }) {
    const [users, setUsers] = useState([])
    const [loading, setLoading] = useState(true)
    const [busy, setBusy] = useState(false)
    const [notice, setNotice] = useState(null) // { type: 'ok' | 'error', text }

    const [username, setUsername] = useState('')
    const [password, setPassword] = useState('')
    const [makeAdmin, setMakeAdmin] = useState(false)

    const loadUsers = useCallback(async () => {
        try {
            const response = await fetch(`${serverUrl}/api/users`, { credentials: 'include' })
            setUsers(await readJson(response))
        } catch (err) {
            console.error('Loading users failed:', err)
            setNotice({ type: 'error', text: `Could not load users. ${errorText(err)}` })
        } finally {
            setLoading(false)
        }
    }, [serverUrl])

    useEffect(() => {
        loadUsers()
    }, [loadUsers])

    async function handleAdd(e) {
        e.preventDefault()
        setBusy(true)
        setNotice(null)
        try {
            const response = await fetch(`${serverUrl}/api/users`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ username, password, is_admin: makeAdmin }),
            })
            const data = await readJson(response)
            setNotice({ type: 'ok', text: `Created user ${data.username}.` })
            setUsername('')
            setPassword('')
            setMakeAdmin(false)
            await loadUsers()
        } catch (err) {
            console.error('Creating user failed:', err)
            setNotice({ type: 'error', text: errorText(err) })
        } finally {
            setBusy(false)
        }
    }

    async function handleDelete(user) {
        if (!window.confirm(`Remove ${user.username}? They will lose access immediately.`)) return
        setBusy(true)
        setNotice(null)
        try {
            const response = await fetch(`${serverUrl}/api/users/${user.id}`, {
                method: 'DELETE',
                credentials: 'include',
            })
            await readJson(response)
            setNotice({ type: 'ok', text: `Removed ${user.username}.` })
            await loadUsers()
        } catch (err) {
            console.error('Removing user failed:', err)
            setNotice({ type: 'error', text: errorText(err) })
        } finally {
            setBusy(false)
        }
    }

    return (
        <details className="user-admin-section">
            <summary>Manage users</summary>

            {loading ? (
                <p className="message">Loading…</p>
            ) : (
                <ul className="user-admin-list">
                    {users.map((u) => {
                        const isSelf = u.username === currentUsername
                        return (
                            <li key={u.id} className="user-admin-row">
                                <span>
                                    {u.username}
                                    {isSelf && <span className="user-admin-tag"> (you)</span>}
                                    {u.is_admin && <span className="user-admin-tag user-admin-tag--admin">admin</span>}
                                </span>
                                <button
                                    type="button"
                                    className="evaluate-btn"
                                    onClick={() => handleDelete(u)}
                                    disabled={busy || isSelf}
                                    title={isSelf ? 'You cannot remove your own account' : undefined}
                                >
                                    Remove
                                </button>
                            </li>
                        )
                    })}
                </ul>
            )}

            <form onSubmit={handleAdd} className="user-admin-form">
                <h3>Add a user</h3>
                <div className="auth-field">
                    <label htmlFor="new-user-username">Username</label>
                    <input
                        id="new-user-username"
                        type="text"
                        autoComplete="off"
                        value={username}
                        onChange={(e) => setUsername(e.target.value)}
                        required
                    />
                </div>
                <div className="auth-field">
                    <label htmlFor="new-user-password">Password (at least 8 characters)</label>
                    <input
                        id="new-user-password"
                        type="password"
                        autoComplete="new-password"
                        minLength={8}
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        required
                    />
                </div>
                <label className="user-admin-checkbox">
                    <input
                        type="checkbox"
                        checked={makeAdmin}
                        onChange={(e) => setMakeAdmin(e.target.checked)}
                    />
                    Make this user an admin
                </label>
                <button type="submit" className="evaluate-btn" disabled={busy}>
                    {busy ? 'Working…' : 'Add user'}
                </button>
            </form>

            {notice && (
                <p
                    className={`user-admin-notice user-admin-notice--${notice.type}`}
                    role={notice.type === 'error' ? 'alert' : 'status'}
                >
                    {notice.text}
                </p>
            )}
        </details>
    )
}

export default UserManagement