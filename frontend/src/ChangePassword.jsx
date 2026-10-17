import { useState } from 'react'
import { describeError, readJson } from './AttendanceUtils'
import './ChangePassword.css'

// Change your own password (any login). With `forced`, it is the only thing the server lets this login do
// until it is done (a new login, or one an admin reset). onChanged runs after the server accepted the change.
function ChangePassword({ serverUrl, forced = false, onChanged, onCancel }) {
    const [current, setCurrent] = useState('')
    const [next, setNext] = useState('')
    const [confirm, setConfirm] = useState('')
    const [busy, setBusy] = useState(false)
    const [error, setError] = useState(null)

    async function handleSubmit(e) {
        e.preventDefault()
        if (next.length < 8) {
            setError('The new password must be at least 8 characters.')
            return
        }
        if (next !== confirm) {
            setError('The new passwords do not match.')
            return
        }
        setBusy(true)
        setError(null)
        try {
            await fetch(`${serverUrl}/api/me/password`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ current_password: current, new_password: next }),
            }).then(readJson)
            setCurrent('')
            setNext('')
            setConfirm('')
            onChanged()
        } catch (err) {
            console.error('Changing password failed:', err)
            setError(describeError(err))
        } finally {
            setBusy(false)
        }
    }

    return (
        <form className="change-password" onSubmit={handleSubmit}>
            <h2 className="change-password-title">{forced ? 'Choose your own password' : 'Change password'}</h2>
            {forced && (
                <p className="attendance-hint">
                    Your account uses a temporary password. Enter it below, then choose a new password only you know.
                </p>
            )}
            <div className="auth-field">
                <label htmlFor="current-password">{forced ? 'Temporary password' : 'Current password'}</label>
                <input
                    id="current-password"
                    type="password"
                    autoComplete="current-password"
                    value={current}
                    onChange={(e) => setCurrent(e.target.value)}
                    required
                />
            </div>
            <div className="auth-field">
                <label htmlFor="new-password">New password</label>
                <input
                    id="new-password"
                    type="password"
                    autoComplete="new-password"
                    value={next}
                    onChange={(e) => setNext(e.target.value)}
                    required
                />
            </div>
            <div className="auth-field">
                <label htmlFor="confirm-new-password">Confirm new password</label>
                <input
                    id="confirm-new-password"
                    type="password"
                    autoComplete="new-password"
                    value={confirm}
                    onChange={(e) => setConfirm(e.target.value)}
                    required
                />
            </div>
            <div className="change-password-buttons">
                <button type="submit" className="evaluate-btn" disabled={busy}>
                    {busy ? 'Saving…' : 'Change password'}
                </button>
                {!forced && onCancel && (
                    <button type="button" className="evaluate-btn" onClick={onCancel} disabled={busy}>
                        Cancel
                    </button>
                )}
            </div>
            {error && <p className="auth-error" role="alert">{error}</p>}
        </form>
    )
}

export default ChangePassword