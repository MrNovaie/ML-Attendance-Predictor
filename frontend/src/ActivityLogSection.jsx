import { useEffect, useMemo, useState } from 'react'
import { describeError, downloadFromServer, readJson } from './AttendanceUtils'
import { ACTION_LABELS, buildLogQuery, formatLogTime, groupEntries } from './ActivityLogUtils'
import './AttendanceForm.css' // shared look: .attendance-hint, .attendance-notice, .evaluate-btn
import './ActivityLogSection.css'

const PAGE_SIZE = 50
const NO_FILTERS = { admin: '', person: '', action: '', from: '', to: '' }

// Activity log (admin, read-only): who changed what, newest first. Filter by admin, person, action and date,
// download what is shown as a CSV, and see whether the log's hash chain still checks out.
// Nothing here can change a log row; the server has no route that does.
function ActivityLogSection({ serverUrl }) {
    const [opened, setOpened] = useState(false) // nothing is fetched until the section is opened

    // filters: `draft` is what is typed in the boxes, `applied` is what the list was last fetched with
    const [draft, setDraft] = useState(NO_FILTERS)
    const [applied, setApplied] = useState(NO_FILTERS)
    const [reload, setReload] = useState(0) // bumped by "Refresh"

    // the list: `page` remembers which request it answers, so "loading" is simply "the answer is not for this request yet"
    const requestKey = JSON.stringify([applied, reload])
    const [page, setPage] = useState({ key: null, entries: [], more: false, error: null })
    const [loadingMore, setLoadingMore] = useState(false)

    // the chain check, same idea
    const [checkAgain, setCheckAgain] = useState(0)
    const [chain, setChain] = useState({ key: null })

    const [downloading, setDownloading] = useState(false)
    const [notice, setNotice] = useState(null)

    useEffect(() => {
        if (!opened) return undefined
        let ignore = false
        const query = buildLogQuery({ ...applied, limit: PAGE_SIZE })
        fetch(`${serverUrl}/api/activity-log?${query}`, { credentials: 'include' })
            .then(readJson)
            .then((data) => {
                if (!ignore) setPage({ key: requestKey, entries: data.entries, more: data.more, error: null })
            })
            .catch((err) => {
                console.error('Loading the activity log failed:', err)
                if (!ignore) setPage({ key: requestKey, entries: [], more: false, error: describeError(err) })
            })
        return () => {
            ignore = true
        }
    }, [serverUrl, opened, applied, requestKey])

    useEffect(() => {
        if (!opened) return undefined
        let ignore = false
        fetch(`${serverUrl}/api/activity-log/verify`, { credentials: 'include' })
            .then(readJson)
            .then((data) => {
                if (!ignore) setChain({ key: checkAgain, ...data })
            })
            .catch((err) => {
                console.error('Checking the activity log failed:', err)
                if (!ignore) setChain({ key: checkAgain, error: describeError(err) })
            })
        return () => {
            ignore = true
        }
    }, [serverUrl, opened, checkAgain, reload])

    const loading = opened && page.key !== requestKey
    const groups = useMemo(() => groupEntries(page.entries), [page.entries])
    const checking = opened && chain.key !== checkAgain
    const rangeBackwards = draft.from && draft.to && draft.from > draft.to

    function setField(name, value) {
        setDraft((prev) => ({ ...prev, [name]: value }))
    }

    function search(event) {
        event.preventDefault()
        setNotice(null)
        setApplied({ ...draft })
    }

    function clear() {
        setNotice(null)
        setDraft(NO_FILTERS)
        setApplied(NO_FILTERS)
    }

    async function loadOlder() {
        const oldest = page.entries[page.entries.length - 1]
        setLoadingMore(true)
        setNotice(null)
        try {
            const query = buildLogQuery({ ...applied, limit: PAGE_SIZE, before: oldest.seq })
            const data = await fetch(`${serverUrl}/api/activity-log?${query}`, { credentials: 'include' }).then(readJson)
            setPage((prev) => ({ ...prev, entries: [...prev.entries, ...data.entries], more: data.more }))
        } catch (err) {
            console.error('Loading older log entries failed:', err)
            setNotice({ type: 'error', text: describeError(err) })
        } finally {
            setLoadingMore(false)
        }
    }

    async function download() {
        setDownloading(true)
        setNotice(null)
        try {
            const query = buildLogQuery(applied)
            const fileName = await downloadFromServer(
                `${serverUrl}/api/activity-log.csv${query ? `?${query}` : ''}`,
                'activity-log.csv',
            )
            setNotice({ type: 'ok', text: `Downloaded ${fileName}.` })
        } catch (err) {
            console.error('Downloading the activity log failed:', err)
            setNotice({ type: 'error', text: describeError(err) })
        } finally {
            setDownloading(false)
        }
    }

    return (
        <details className="log-section" onToggle={(event) => setOpened(event.currentTarget.open)}>
            <summary>Activity log</summary>
            <section className="log-body">
                <p className="attendance-hint">
                    Every change an admin makes, newest first. Nothing here can be edited or deleted.
                </p>

                <ChainStatus chain={chain} checking={checking} onCheck={() => setCheckAgain((n) => n + 1)} />

                <form className="log-filters" onSubmit={search}>
                    <label>
                        Admin name
                        <input type="text" value={draft.admin} onChange={(e) => setField('admin', e.target.value)} />
                    </label>
                    <label>
                        Person name
                        <input type="text" value={draft.person} onChange={(e) => setField('person', e.target.value)} />
                    </label>
                    <label>
                        Action
                        <select value={draft.action} onChange={(e) => setField('action', e.target.value)}>
                            <option value="">All actions</option>
                            {Object.entries(ACTION_LABELS).map(([value, label]) => (
                                <option key={value} value={value}>
                                    {label}
                                </option>
                            ))}
                        </select>
                    </label>
                    <label>
                        From
                        <input type="date" value={draft.from} onChange={(e) => setField('from', e.target.value)} />
                    </label>
                    <label>
                        To
                        <input type="date" value={draft.to} onChange={(e) => setField('to', e.target.value)} />
                    </label>
                    <div className="log-buttons">
                        <button type="submit" className="evaluate-btn">
                            Search
                        </button>
                        <button type="button" className="evaluate-btn" onClick={clear}>
                            Clear
                        </button>
                    </div>
                </form>
                {rangeBackwards && <p className="attendance-hint">“From” is after “To”, so nothing will match.</p>}

                <div className="log-toolbar">
                    <button type="button" className="evaluate-btn" onClick={() => setReload((n) => n + 1)} disabled={loading}>
                        Refresh
                    </button>
                    <button
                        type="button"
                        className="evaluate-btn"
                        onClick={download}
                        disabled={downloading}
                        aria-busy={downloading}
                    >
                        {downloading ? 'Preparing…' : 'Download CSV'}
                    </button>
                </div>
                <p className="attendance-hint">The CSV holds everything that matches the filters above, not just this page.</p>

                {notice && (
                    <p className={`attendance-notice attendance-notice--${notice.type}`} role={notice.type === 'error' ? 'alert' : 'status'}>
                        {notice.text}
                    </p>
                )}

                {page.error && (
                    <p className="attendance-notice attendance-notice--error" role="alert">
                        {page.error}
                    </p>
                )}
                {loading && <p className="attendance-hint">Loading…</p>}
                {!loading && !page.error && groups.length === 0 && (
                    <p className="attendance-hint">No entries match.</p>
                )}

                <ul className="log-list">
                    {groups.map((group) =>
                        group.summary ? (
                            <li key={group.key} className="log-item">
                                <details>
                                    <summary>
                                        <time dateTime={group.rows[0].logged_at} title={group.rows[0].logged_at}>
                                            {formatLogTime(group.rows[0].logged_at)}
                                        </time>
                                        <span className="log-text">{group.summary}</span>
                                    </summary>
                                    <ul className="log-sublist">
                                        {group.rows.map((row) => (
                                            <li key={row.seq}>
                                                <span className="log-seq">#{row.seq}</span> {row.text}
                                            </li>
                                        ))}
                                    </ul>
                                </details>
                            </li>
                        ) : (
                            <li key={group.key} className="log-item">
                                <time dateTime={group.rows[0].logged_at} title={group.rows[0].logged_at}>
                                    {formatLogTime(group.rows[0].logged_at)}
                                </time>
                                <span className="log-text">
                                    {group.rows[0].text} <span className="log-seq">#{group.rows[0].seq}</span>
                                </span>
                            </li>
                        ),
                    )}
                </ul>

                {!loading && page.more && (
                    <button type="button" className="evaluate-btn" onClick={loadOlder} disabled={loadingMore} aria-busy={loadingMore}>
                        {loadingMore ? 'Loading…' : 'Load older entries'}
                    </button>
                )}
            </section>
        </details>
    )
}

// The banner at the top: is the hash chain intact? `tail_checked` is false until a copy of the newest hash is kept
// off the server, so an intact result still cannot rule out entries cut off the END of the log.
function ChainStatus({ chain, checking, onCheck }) {
    if (checking) return <p className="log-chain" role="status">Checking the log…</p>
    if (chain.error) {
        return (
            <p className="log-chain log-chain--bad" role="alert">
                Could not check the log: {chain.error}{' '}
                <button type="button" className="log-link" onClick={onCheck}>Try again</button>
            </p>
        )
    }
    if (!chain.ok) {
        return (
            <p className="log-chain log-chain--bad" role="alert">
                <strong>The log has been altered.</strong> The first entry that does not match is #{chain.first_bad_seq}.
                Entries after that cannot be trusted. {' '}
                <button type="button" className="log-link" onClick={onCheck}>Check again</button>
            </p>
        )
    }
    return (
        <p className="log-chain log-chain--ok" role="status">
            <strong>Log intact:</strong> The {chain.rows} {chain.rows === 1 ? 'entry matches' : 'entries match'} their hashes.
            {!chain.tail_checked && ' Entries removed from the very end of the log cannot be detected yet.'}{' '}
            <button type="button" className="log-link" onClick={onCheck}>Check again</button>
        </p>
    )
}

export default ActivityLogSection
