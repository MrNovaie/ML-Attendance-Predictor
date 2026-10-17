import { useState, useEffect, useCallback, useMemo } from 'react'
import './App.css'
import AttendanceForm from './AttendanceForm'
import PeopleSection from './PeopleSection'
import BackupSection from './BackupSection'
import AccountsSection from './AccountsSection'
import ChangePassword from './ChangePassword'
import UserManagement from './UserManagement'
import DailyAttendance from './DailyAttendance'
import FilterBar from './FilterBar'
import { matchesSearch } from './AttendanceUtils'

const SERVER_URL = 'http://localhost:5000'

function App() {
    const targetDate = new Date()
    targetDate.setDate(targetDate.getDate() + 1)
    const [isAdmin, setIsAdmin] = useState(false)
    const [date, setDate] = useState(targetDate.toISOString().split('T')[0])
    const [predictions, setPredictions] = useState([])
    const [people, setPeople] = useState([]) // [{ id, name, department }], admins only: used by the department filter
    const [department, setDepartment] = useState('') // '' = all departments
    const [search, setSearch] = useState('')
    const [message, setMessage] = useState('')
    const [evalLoading, setEvalLoading] = useState(false)
    const [evalResult, setEvalResult] = useState(null)
    const [evalError, setEvalError] = useState(null)
    const [user, setUser] = useState(null)
    const [authChecking, setAuthChecking] = useState(true)
    const [authView, setAuthView] = useState('login')
    const [authNotice, setAuthNotice] = useState('')
    const [authOptions, setAuthOptions] = useState({ registration_open: false, departments: [], default_department: 'Holding' })
    const [mustChange, setMustChange] = useState(false) // this login has a temporary password and must pick its own
    const [showChangePassword, setShowChangePassword] = useState(false)
    const [passwordNotice, setPasswordNotice] = useState('')
    const [view, setView] = useState('predictions') // 'predictions' | 'attendance'
    const [predictionsVersion, setPredictionsVersion] = useState(0) // bumped after attendance is saved
    const [peopleVersion, setPeopleVersion] = useState(0) // bumped after the People section adds a person or moves a link

    const daysOfWeek = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
    const dayName = date ? daysOfWeek[new Date(date).getDay()] : ''

    // Predictions are admin-only, so everyone else lives on the attendance page and has no way to switch.
    const activeView = isAdmin ? view : 'attendance'

    // Department of each person, and the departments somebody is actually in
    const departmentOf = useMemo(() => new Map(people.map((p) => [p.name, p.department])), [people])
    const departments = useMemo(
        () => [...new Set(people.map((p) => p.department).filter(Boolean))].sort(),
        [people],
    )
    const passesFilter = useCallback(
        (name) => (!department || departmentOf.get(name) === department) && matchesSearch(name, search),
        [department, departmentOf, search],
    )
    const shownPredictions = predictions.filter((prediction) => passesFilter(prediction.person))
    const filtering = Boolean(department || search.trim())

    const refreshUser = useCallback(async () => {
        try {
            const response = await fetch(`${SERVER_URL}/api/me`, { credentials: 'include' })
            if (!response.ok) {
                setUser(null)
                setIsAdmin(false)
                setMustChange(false)
                return
            }
            const data = await response.json()
            setUser(data.username)
            setIsAdmin(data.is_admin)
            setMustChange(Boolean(data.must_change_password))
        } catch (err) {
            console.error('Session check failed:', err)
            setUser(null)
            setIsAdmin(false)
            setMustChange(false)
        }
    }, [])

    useEffect(() => {
        refreshUser().finally(() => setAuthChecking(false))
    }, [refreshUser])

    // The login screen asks whether sign-up is open (it is closed unless the server's ALLOW_REGISTRATION is set to 1).
    useEffect(() => {
        if (authChecking || user) return
        fetch(`${SERVER_URL}/api/auth/options`, { credentials: 'include' })
            .then((response) => (response.ok ? response.json() : null))
            .then((data) => {
                if (data) setAuthOptions(data)
            })
            .catch((err) => console.error('Loading sign-in options failed:', err))
    }, [authChecking, user])

    useEffect(() => {
        if (!user || !isAdmin || !date) return

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
    }, [user, isAdmin, date, predictionsVersion])

    // Who exists and which department they are in (for the filter). Reloaded when someone is added or moved.
    useEffect(() => {
        if (!user || !isAdmin) return
        fetch(`${SERVER_URL}/api/people`, { credentials: 'include' })
            .then((response) => (response.ok ? response.json() : []))
            .then(setPeople)
            .catch((err) => console.error('Loading people failed:', err))
    }, [user, isAdmin, peopleVersion, predictionsVersion])

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

    // Clears everything that belongs to one login, whether it logs out or the screen is shown to the next person
    function resetSession() {
        setUser(null)
        setIsAdmin(false)
        setMustChange(false)
        setView('predictions')
        setDepartment('')
        setSearch('')
        setPeople([])
        setShowChangePassword(false)
        setPasswordNotice('')
    }

    if (!user) {
        return (
            <div className="app-container auth-container">
                <h1 className="title">Attendance Predictor</h1>
                {authNotice && <p className="auth-notice">{authNotice}</p>}
                {authView === 'login' || !authOptions.registration_open ? (
                    <LoginForm
                        onSuccess={refreshUser}
                        signupOpen={authOptions.registration_open}
                        onSwitchRegister={() => {
                            setAuthNotice('')
                            setAuthView('register')
                        }}
                    />
                ) : (
                    <RegisterForm
                        departments={authOptions.departments.filter((d) => d !== authOptions.default_department)}
                        defaultDepartment={authOptions.default_department}
                        onSuccess={(askedForDepartment) => {
                            setAuthNotice(
                                askedForDepartment
                                    ? 'Account created. An admin will confirm your department. Please log in.'
                                    : 'Account created. Please log in.',
                            )
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

    // A temporary password (new login, or reset by an admin): nothing else is shown until the user has chosen their own.
    // The server refuses every other request from this login in the meantime.
    if (mustChange) {
        return (
            <div className="app-container auth-container">
                <h1 className="title">Attendance Predictor</h1>
                <p className="auth-notice">Welcome, {user}.</p>
                <ChangePassword serverUrl={SERVER_URL} forced onChanged={refreshUser} />
                <LogoutButton onLogout={resetSession} />
            </div>
        )
    }

    return (
        <div className="app-container">
            <div className="auth-bar">
                <span>Welcome back, <strong style={{ color: 'cyan', fontFamily: 'cursive'}}>{user}</strong></span>
                <span className="auth-bar-buttons">
                    <button
                        type="button"
                        className="evaluate-btn auth-logout-btn"
                        onClick={() => {
                            setPasswordNotice('')
                            setShowChangePassword((open) => !open)
                        }}
                        aria-expanded={showChangePassword}
                    >
                        Change password
                    </button>
                    <LogoutButton onLogout={resetSession} />
                </span>
            </div>
            {showChangePassword && (
                <ChangePassword
                    serverUrl={SERVER_URL}
                    onChanged={() => {
                        setShowChangePassword(false)
                        setPasswordNotice('Password changed.')
                    }}
                    onCancel={() => setShowChangePassword(false)}
                />
            )}
            {passwordNotice && <p className="auth-notice" role="status">{passwordNotice}</p>}
            {activeView === 'attendance' ? (
                <DailyAttendance
                    serverUrl={SERVER_URL}
                    isAdmin={isAdmin}
                    onSaved={() => setPredictionsVersion((v) => v + 1)}
                />
            ) : (
                <>
                    <h1 className="title">Attendance Predictor</h1>
                    <FilterBar
                        departments={departments}
                        department={department}
                        onDepartmentChange={setDepartment}
                        search={search}
                        onSearchChange={setSearch}
                        date={date}
                        onDateChange={setDate}
                    />
                    <p className="message">{message}</p>
                    {filtering && predictions.length > 0 && (
                        <p className="filter-summary" role="status">
                            {shownPredictions.length === 0
                                ? 'No one matches the filter.'
                                : `Showing ${shownPredictions.length} of ${predictions.length}`}
                        </p>
                    )}
                    <ul className="predictions-list">
                        {shownPredictions.map((prediction) => (
                            <li key={prediction.person} className ="prediction-item">
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
                                    {evalResult.evaluations.filter((row) => passesFilter(row.person)).map((row) => (
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
                    {isAdmin && <AttendanceForm serverUrl={SERVER_URL} peopleVersion={peopleVersion} />}
                    {isAdmin && (
                        <PeopleSection serverUrl={SERVER_URL} onPeopleChanged={() => setPeopleVersion((v) => v + 1)} />
                    )}
                    {isAdmin && (
                        <AccountsSection
                            serverUrl={SERVER_URL}
                            peopleVersion={peopleVersion}
                            onPeopleChanged={() => setPeopleVersion((v) => v + 1)}
                        />
                    )}
                    {isAdmin && <BackupSection serverUrl={SERVER_URL} />}
                    {isAdmin && <UserManagement serverUrl={SERVER_URL} currentUsername={user} />}
                </>
            )}
            {isAdmin && (
                <button
                    type="button"
                    className="view-toggle"
                    onClick={() => setView(activeView === 'attendance' ? 'predictions' : 'attendance')}
                >
                    {activeView === 'attendance' ? 'Predictions' : "Today's Attendance"}
                </button>
            )}
            <div className="view-toggle-spacer" aria-hidden="true" />
        </div>
    )
}

function LoginForm({ onSuccess, onSwitchRegister, signupOpen }) {
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
            {signupOpen ? (
                <button type="button" className="auth-link" onClick={onSwitchRegister}>
                    Need an account? Register
                </button>
            ) : (
                <p className="auth-hint">No account yet? Ask an admin to create one for you.</p>
            )}
        </div>
    )
}

function RegisterForm({ onSuccess, onSwitchLogin, departments, defaultDepartment }) {
    const [username, setUsername] = useState('')
    const [password, setPassword] = useState('')
    const [confirmPassword, setConfirmPassword] = useState('')
    const [requestedDepartment, setRequestedDepartment] = useState('') // '' = no request
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
                body: JSON.stringify({ username, password, confirmPassword, department: requestedDepartment }),
            }) 
            const data = await response.json()
            if (!response.ok) {
                setError(data.error || `HTTP ${response.status}`)
                return
            }
            onSuccess(Boolean(requestedDepartment))
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
                <div className="auth-field">
                    <label htmlFor="register-department">Department (optional)</label>
                    <select
                        id="register-department"
                        value={requestedDepartment}
                        onChange={(e) => setRequestedDepartment(e.target.value)}
                    >
                        <option value="">No request ({defaultDepartment})</option>
                        {departments.map((d) => (
                            <option key={d} value={d}>
                                {d}
                            </option>
                        ))}
                    </select>
                    <small>Confirmed by an admin.</small>
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