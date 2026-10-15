import { useState, useEffect, useCallback } from 'react'
import './App.css'
import './AttendanceForm'
import './UserManagement'
import AttendanceForm from './AttendanceForm'
import UserManagement from './UserManagement'
import DailyAttendance from './DailyAttendance'

const SERVER_URL = 'http://localhost:5000'

function App() {
    const targetDate = new Date()
    targetDate.setDate(targetDate.getDate() + 1)
    const [isAdmin, setIsAdmin] = useState(false)
    const [date, setDate] = useState(targetDate.toISOString().split('T')[0])
    const [predictions, setPredictions] = useState([])
    const [message, setMessage] = useState('')
    const [evalLoading, setEvalLoading] = useState(false)
    const [evalResult, setEvalResult] = useState(null)
    const [evalError, setEvalError] = useState(null)
    const [user, setUser] = useState(null)
    const [authChecking, setAuthChecking] = useState(true)
    const [authView, setAuthView] = useState('login')
    const [authNotice, setAuthNotice] = useState('')
    const [view, setView] = useState('predictions') // 'predictions' | 'attendance'
    const [predictionsVersion, setPredictionsVersion] = useState(0) // bumped after attendance is saved

    const daysOfWeek = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
    const dayName = date ? daysOfWeek[new Date(date).getDay()] : ''

    const refreshUser = useCallback(async () => {
        try {
            const response = await fetch(`${SERVER_URL}/api/me`, { credentials: 'include' })
            if (!response.ok) {
                setUser(null)
                setIsAdmin(false)
                return
            }
            const data = await response.json()
            setUser(data.username)
            setIsAdmin(data.is_admin)
        } catch (err) {
            console.error('Session check failed:', err)
            setUser(null)
            setIsAdmin(false)
        }
    }, [])

    useEffect(() => {
        refreshUser().finally(() => setAuthChecking(false))
    }, [refreshUser])

    useEffect(() => {
        if (!user || !date) return

        fetch(`${SERVER_URL}/api/predict?date=${date}`, { credentials: 'include' })
            .then(async (response) => {
                const data = await response.json()
                if (!response.ok) {
                    setPredictions([])
                    setMessage(data.error || `HTTP ${response.status}`)
                    return
                }
                setPredictions(data.predictions)
                setMessage(data.message)
            })
            .catch((err) => {
                console.error('Network error:', err)
                setPredictions([])
                setMessage('Could not reach the server.')
            })
    }, [user, date, predictionsVersion])

    async function runEvaluation() {
        setEvalLoading(true)
        setEvalError(null)
        try {
            const response = await fetch(`${SERVER_URL}/api/evaluate`, { credentials: 'include' })
            const data = await response.json()
            if (!response.ok) {
                setEvalResult(null)
                setEvalError(data.error || `HTTP ${response.status}`)
                return
            }
            setEvalResult(data)
        } catch (err) {
            console.error('Evaluate request failed:', err)
            setEvalResult(null)
            setEvalError('Could not reach the server.')
        } finally {
            setEvalLoading(false)
        }
    }

    if (authChecking) {
        return (
            <div className="app-container">
                <p className="message">Checking session…</p>
            </div>
        )
    }

    if (!user) {
        return (
            <div className="app-container auth-container">
                <h1 className="title">Attendance Predictor</h1>
                {authNotice && <p className="auth-notice">{authNotice}</p>}
                {authView === 'login' ? (
                    <LoginForm
                        onSuccess={refreshUser}
                        onSwitchRegister={() => {
                            setAuthNotice('')
                            setAuthView('register')
                        }}
                    />
                ) : (
                    <RegisterForm
                        onSuccess={() => {
                            setAuthNotice('Account created. Please log in.')
                            setAuthView('login')
                        }}
                        onSwitchLogin={() => {
                            setAuthNotice('')
                            setAuthView('login')
                        }}
                    />
                )}
            </div>
        )
    }

    return (
        <div className="app-container">
            <div className="auth-bar">
                <span>Welcome back, <strong style={{ color: 'cyan', fontFamily: 'cursive'}}>{user}</strong></span>
                <LogoutButton onLogout={() => {setUser(null); setIsAdmin(false); setView('predictions')} } />
            </div>
            {view === 'attendance' ? (
                <DailyAttendance
                    serverUrl={SERVER_URL}
                    isAdmin={isAdmin}
                    onSaved={() => setPredictionsVersion((v) => v + 1)}
                />
            ) : (
                <>
                    <h1 className="title">Attendance Predictor</h1>
                    <label htmlFor="date-input" className="date-input">Select a date: </label>
                    <input
                        id="date-input"
                        type="date"
                        value={date}
                        onChange={(e) => setDate(e.target.value)}
                    />
                    <p className="message">{message}</p>
                    <ul className="predictions-list">
                        {predictions.map((prediction, index) => (
                            <li key={index} className ="prediction-item">
                                {prediction.person}: <span className={`badge badge-${prediction.verdict.toLowerCase().replaceAll(' ', '-')}`}> {prediction.verdict} </span> 
                                <span className="bar-text">Confidence Score</span>
                                <div className="bar-bg">
                                    <div className="bar-fill" style={{ width: `${prediction.p_winner}%` }} />
                                </div>
                                <small>
                                    based on <strong>{prediction.sample_size}</strong> past {dayName}s
                                </small>
                            </li>
                        ))}
                    </ul>
                    <div className="evaluate-section">
                        <button
                            type="button"
                            className="evaluate-btn"
                            onClick={runEvaluation}
                            disabled={evalLoading}
                            aria-busy={evalLoading}
                        >
                            {evalLoading ? 'Evaluating…' : 'Check model accuracy (using historical data)'}
                        </button>
                        {evalError && (
                            <p className="eval-message eval-message--error" role="alert">{evalError}</p>
                        )}
                        {evalResult?.evaluations?.length > 0 && (
                            <details open className="eval-details">
                                <summary>Model accuracy (holdout test)</summary>
                                <ul className="eval-list">
                                    {evalResult.evaluations.map((row) => (
                                        <li key={row.person} className="eval-item">
                                            <strong>{row.person}</strong>: <span className="badge badge-eval">{row.accuracy_pct}% accuracy</span>{' '}
                                            (n={row.n_samples})
                                            <br />
                                            (baseline: {row.baseline_pct}%, n={row.total_samples})
                                        </li>
                                    ))}
                                </ul>
                                <div className="eval-qa-container">
                                    <h3>QnA</h3>
                                    <p><strong>What is the confidence score?</strong></p>
                                    <small>
                                        A: The confidence score is the model&apos;s estimated probability for the
                                        predicted outcome (e.g. In) to happen on this day (e.g. {dayName}). The higher the number, the more sure the model is.
                                    </small>
                                    <p><strong>What is the holdout set?</strong></p>
                                    <small>
                                        A: The holdout set is a random 20% of the data set aside before training.
                                        The model never sees these rows while learning, so they give an unbiased
                                        check of how well it predicts new data. With only {evalResult.total_samples}{' '}
                                        rows, the holdout is just ~{evalResult.n_samples} rows per person, which
                                        means the accuracy number bounces around a lot.
                                    </small>
                                    <p><strong>What is the accuracy score?</strong></p>
                                    <small>
                                        A: The accuracy score is the percentage of holdout-set days the model
                                        predicted correctly. A useful model should beat the baseline below.
                                    </small>
                                    <p><strong>What is the baseline?</strong></p>
                                    <small>
                                        A: The baseline is the percentage of past days (all days, not just {dayName}s)
                                        that the model would have predicted correctly if it had just guessed the most
                                        frequent class (the mode).
                                    </small>
                                </div>
                            </details>
                        )}
                    </div>
                    {isAdmin && <AttendanceForm serverUrl={SERVER_URL} />}
                    {isAdmin && <UserManagement serverUrl={SERVER_URL} currentUsername={user} />}
                </>
            )}
            <button
                type="button"
                className="view-toggle"
                onClick={() => setView(view === 'attendance' ? 'predictions' : 'attendance')}
            >
                {view === 'attendance' ? 'Predictions' : "Today's Attendance"}
            </button>
            <div className="view-toggle-spacer" aria-hidden="true" />
        </div>
    )
}

function LoginForm({ onSuccess, onSwitchRegister }) {
    const [username, setUsername] = useState('')
    const [password, setPassword] = useState('')
    const [error, setError] = useState(null)
    const [loading, setLoading] = useState(false)

    async function handleSubmit(e) {
        e.preventDefault()
        setLoading(true)
        setError(null)
        try {
            const response = await fetch(`${SERVER_URL}/api/login`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ username, password }),
            })
            const data = await response.json()
            if (!response.ok) {
                setError(data.error || `HTTP ${response.status}`)
                return
            }
            await onSuccess()
        } catch (err) {
            console.error('Login request failed:', err)
            setError('Could not reach the server.')
        } finally {
            setLoading(false)
        }
    }

    return (
        <div className="auth-form">
            <h2>Login</h2>
            <form onSubmit={handleSubmit}>
                <div className="auth-field">
                    <label htmlFor="login-username">Username</label>
                    <input
                        id="login-username"
                        type="text"
                        autoComplete="username"
                        placeholder="Enter your username"
                        value={username}
                        onChange={(e) => setUsername(e.target.value)}
                        required
                    />
                </div>
                <div className="auth-field">
                    <label htmlFor="login-password">Password</label>
                    <input
                        id="login-password"
                        type="password"
                        autoComplete="current-password"
                        placeholder="Enter your password"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        required
                    />
                </div>
                <button type="submit" className="evaluate-btn" disabled={loading}>
                    {loading ? 'Logging in…' : 'Login'}
                </button>
            </form>
            {error && <p className="auth-error" role="alert">{error}</p>}
            <button type="button" className="auth-link" onClick={onSwitchRegister}>
                Need an account? Register
            </button>
        </div>
    )
}

function RegisterForm({ onSuccess, onSwitchLogin }) {
    const [username, setUsername] = useState('')
    const [password, setPassword] = useState('')
    const [confirmPassword, setConfirmPassword] = useState('')
    const [error, setError] = useState(null)
    const [loading, setLoading] = useState(false)

    async function handleSubmit(e) {
        e.preventDefault()
        if (password !== confirmPassword) {
            setError('Passwords do not match.')
            return
        }
        setLoading(true)
        setError(null)
        try {
            const response = await fetch(`${SERVER_URL}/api/register`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ username, password, confirmPassword }),
            }) 
            const data = await response.json()
            if (!response.ok) {
                setError(data.error || `HTTP ${response.status}`)
                return
            }
            onSuccess()
        } catch (err) {
            console.error('Register request failed:', err)
            setError('Could not reach the server.')
        } finally {
            setLoading(false)
        }
    }

    return (
        <div className="auth-form">
            <h2>Register</h2>
            <form onSubmit={handleSubmit}>
                <div className="auth-field">
                    <label htmlFor="register-username">Username</label>
                    <input
                        id="register-username"
                        type="text"
                        autoComplete="username"
                        placeholder="Choose a username"
                        value={username}
                        onChange={(e) => setUsername(e.target.value)}
                        required
                    />
                </div>
                <div className="auth-field">
                    <label htmlFor="register-password">Password</label>
                    <input
                        id="register-password"
                        type="password"
                        autoComplete="new-password"
                        placeholder="Choose a password"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        required
                    />
                </div>
                <div className="auth-field">
                    <label htmlFor="register-confirm">Confirm password</label>
                    <input
                        id="register-confirm"
                        type="password"
                        autoComplete="new-password"
                        placeholder="Confirm your password"
                        value={confirmPassword}
                        onChange={(e) => setConfirmPassword(e.target.value)}
                        required
                    />
                </div>
                <button type="submit" className="evaluate-btn" disabled={loading}>
                    {loading ? 'Creating account…' : 'Register'}
                </button>
            </form>
            {error && <p className="auth-error" role="alert">{error}</p>}
            <button type="button" className="auth-link" onClick={onSwitchLogin}>
                Already have an account? Log in
            </button>
        </div>
    )
}

function LogoutButton({ onLogout }) {
    const [loading, setLoading] = useState(false)

    async function handleLogout() {
        setLoading(true)
        try {
            await fetch(`${SERVER_URL}/api/logout`, {
                method: 'POST',
                credentials: 'include',
            })
        } catch (err) {
            console.error('Logout request failed:', err)
        } finally {
            setLoading(false)
            onLogout()
        }
    }

    return (
        <button type="button" className="evaluate-btn auth-logout-btn" onClick={handleLogout} disabled={loading}>
            {loading ? 'Logging out…' : 'Log out'}
        </button>
    )
}

export default App