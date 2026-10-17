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

    // Reset password: one row at a time opens a small form under it
    const [resetId, setResetId] = useState(null) // id of the login whose reset form is open
    const [resetPassword, setResetPassword] = useState('') // blank = generate one
    const [resetResult, setResetResult] = useState(null) // { username, temporary_password }, shown once

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

    async function handleReset(e, user) {
        e.preventDefault()
        setBusy(true)
        setNotice(null)
        setResetResult(null)
        try {
            const response = await fetch(`${serverUrl}/api/users/${user.id}/password`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ password: resetPassword }),
            })
            const data = await readJson(response)
            setResetResult({ username: data.username, temporary_password: data.temporary_password })
            setResetId(null)
            setResetPassword('')
            await loadUsers()
        } catch (err) {
            console.error('Resetting password failed:', err)
            setNotice({ type: 'error', text: errorText(err) })
        } finally {
            setBusy(false)
        }
    }

    async function handleCopy(text) {
        try {
            await navigator.clipboard.writeText(text)
            setNotice({ type: 'ok', text: 'Copied.' })
        } catch {
            setNotice({ type: 'error', text: 'Could not copy. Select the password and copy it by hand.' })
        }
    }

    function toggleReset(user) {
        setNotice(null)
        setResetResult(null)
        setResetPassword('')
        setResetId(resetId === user.id ? null : user.id)
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
                                    {u.must_change_password && <span className="user-admin-tag"> temporary password</span>}
                                </span>
                                <span className="user-admin-actions">
                                    <button
                                        type="button"
                                        className="evaluate-btn"
                                        onClick={() => toggleReset(u)}
                                        disabled={busy || isSelf}
                                        aria-expanded={resetId === u.id}
                                        title={isSelf ? 'To change your own password, use Change password at the top' : undefined}
                                    >
                                        Reset password
                                    </button>
                                    <button
                                        type="button"
                                        className="evaluate-btn"
                                        onClick={() => handleDelete(u)}
                                        disabled={busy || isSelf}
                                        title={isSelf ? 'You cannot remove your own account' : undefined}
                                    >
                                        Remove
                                    </button>
                                </span>
                                {resetId === u.id && (
                                    <form className="user-admin-reset" onSubmit={(e) => handleReset(e, u)}>
                                        <label htmlFor={`reset-password-${u.id}`}>
                                            Temporary password for {u.username}
                                        </label>
                                        <input
                                            id={`reset-password-${u.id}`}
                                            type="text"
                                            autoComplete="off"
                                            placeholder="Leave blank to generate one"
                                            value={resetPassword}
                                            onChange={(e) => setResetPassword(e.target.value)}
                                        />
                                        <small>
                                            They are signed out everywhere and must choose their own password at next login.
                                        </small>
                                        <span className="user-admin-actions">
                                            <button type="submit" className="evaluate-btn" disabled={busy}>
                                                {busy ? 'Working…' : 'Reset'}
                                            </button>
                                            <button type="button" className="evaluate-btn" onClick={() => toggleReset(u)} disabled={busy}>
                                                Cancel
                                            </button>
                                        </span>
                                    </form>
                                )}
                            </li>
                        )
                    })}
                </ul>
            )}

            {resetResult && (
                <p className="user-admin-result" role="status">
                    Temporary password for <strong>{resetResult.username}</strong>: <code>{resetResult.temporary_password}</code>{' '}
                    <button type="button" className="evaluate-btn" onClick={() => handleCopy(resetResult.temporary_password)}>
                        Copy
                    </button>{' '}
                    <button type="button" className="evaluate-btn" onClick={() => setResetResult(null)}>
                        Hide
                    </button>
                    <br />
                    <small>Shown once. Must change at next login.</small>
                </p>
            )}

            <form onSubmit={handleAdd} className="user-admin-form">
                <h3>Add a user</h3>
                <p className="user-admin-hint">They must choose their own password the first time they log in.</p>
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
                    Make user admin
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