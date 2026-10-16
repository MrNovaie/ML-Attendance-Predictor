import { useEffect, useMemo, useRef, useState } from 'react'
import {
    DndContext,
    DragOverlay,
    KeyboardSensor,
    MouseSensor,
    TouchSensor,
    useDraggable,
    useDroppable,
    useSensor,
    useSensors,
} from '@dnd-kit/core'
import AttendanceTally from './AttendanceTally'
import MyAttendance from './MyAttendance'
import {
    STATUSES,
    clsForStatus,
    describeError,
    downloadBackup,
    readJson,
    shortDate,
    weekendMessage,
} from './AttendanceUtils'
import './DailyAttendance.css'

const TRAY_ID = 'zone-tray'

function zoneToStatus(zoneId) {
    // 'zone-1' -> 1, 'zone-tray' -> undefined (Not Recorded)
    const key = String(zoneId).replace('zone-', '')
    return key === 'tray' ? undefined : Number(key)
}

function countPeople(n) {
    return n === 1 ? '1 person' : `${n} people`
}

function saveMessage({ saved, removed }) {
    if (saved && removed) return `Saved attendance for ${countPeople(saved)} and removed ${removed}.`
    if (removed) return `Removed attendance for ${countPeople(removed)}.`
    if (saved) return `Saved attendance for ${countPeople(saved)}.`
    return 'Nothing to change.'
}

function DropZone({ id, className, children }) {
    const { setNodeRef, isOver } = useDroppable({ id })
    return (
        <div ref={setNodeRef} className={`${className}${isOver ? ' is-over' : ''}`}>
            {children}
        </div>
    )
}

function PersonChip({ person, status, dirty }) {
    const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
        id: `person-${person.id}`,
        data: { personId: person.id },
    })
    const classes = [
        'daily-chip',
        `daily-chip--${clsForStatus(status)}`,
        'is-draggable',
        dirty ? 'is-dirty' : '',
        isDragging ? 'is-dragging' : '',
    ]
        .filter(Boolean)
        .join(' ')
    return (
        <div ref={setNodeRef} className={classes} {...listeners} {...attributes}>
            {person.name}
        </div>
    )
}

// Admin view: drag names between In / Partial Day / Out, or back to Not Recorded, then Save.
function AdminBoard({ serverUrl, onSaved }) {
    // "Today" as the server sees it (UTC+8), so it does not depend on this browser's timezone.
    const [clock, setClock] = useState(null) // { date, weekend }
    const [people, setPeople] = useState([]) // [{ id, name, department }]
    const [saved, setSaved] = useState({}) // what the server has: { [personId]: 0 | 1 | 2 }
    const [draft, setDraft] = useState({}) // what the board shows right now
    const [department, setDepartment] = useState('') // '' = all departments
    const [loading, setLoading] = useState(true)
    const [saving, setSaving] = useState(false)
    const [notice, setNotice] = useState(null) // { type: 'ok' | 'error', text }
    const [activeId, setActiveId] = useState(null)
    const [confirming, setConfirming] = useState(false) // asking "really remove these?" before saving
    const [backupBusy, setBackupBusy] = useState(false)
    const cancelRef = useRef(null)

    // Mouse needs a small movement before a drag starts. Touch needs a short press, so scrolling
    // the page by swiping over a chip still works.
    const sensors = useSensors(
        useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
        useSensor(TouchSensor, { activationConstraint: { delay: 200, tolerance: 8 } }),
        useSensor(KeyboardSensor),
    )

    useEffect(() => {
        let ignore = false
        async function load() {
            try {
                const today = await fetch(`${serverUrl}/api/today`, { credentials: 'include' }).then(readJson)
                if (ignore) return
                setClock(today)
                if (today.weekend) return
                const [peopleData, attendanceData] = await Promise.all([
                    fetch(`${serverUrl}/api/people`, { credentials: 'include' }).then(readJson),
                    fetch(`${serverUrl}/api/attendance?date=${today.date}`, { credentials: 'include' }).then(readJson),
                ])
                if (ignore) return
                const map = Object.fromEntries(attendanceData.records.map((r) => [r.person_id, r.status]))
                setPeople(peopleData)
                setSaved(map)
                setDraft(map)
            } catch (err) {
                console.error('Loading daily attendance failed:', err)
                if (!ignore) setNotice({ type: 'error', text: describeError(err) })
            } finally {
                if (!ignore) setLoading(false)
            }
        }
        load()
        return () => {
            ignore = true
        }
    }, [serverUrl])

    // Departments that someone is actually in
    const departments = useMemo(
        () => [...new Set(people.map((p) => p.department).filter(Boolean))].sort(),
        [people],
    )

    const visible = useMemo(
        () =>
            people
                .filter((p) => !department || p.department === department)
                .sort((a, b) => a.name.localeCompare(b.name)),
        [people, department],
    )

    // Everyone whose placement differs from the server. This covers people hidden by the filter,
    // and people dragged back to Not Recorded (draft undefined while the server has a status).
    const changed = people.filter((p) => draft[p.id] !== saved[p.id])
    // Saved records that this Save would delete (dragged back to Not Recorded). Deleting is permanent.
    const removals = changed.filter((p) => draft[p.id] === undefined && saved[p.id] !== undefined)
    const showConfirm = confirming && removals.length > 0
    const unrecorded = visible.filter((p) => draft[p.id] === undefined)
    const activePerson = people.find((p) => `person-${p.id}` === activeId)

    // Head-count for the department shown, as it stands on screen (including unsaved moves).
    const tally = useMemo(() => {
        const byStatus = {}
        let notRecorded = 0
        for (const p of visible) {
            const s = draft[p.id]
            if (s === undefined) notRecorded += 1
            else byStatus[s] = (byStatus[s] ?? 0) + 1
        }
        return { byStatus, notRecorded, total: visible.length }
    }, [visible, draft])

    function handleDragEnd({ active, over }) {
        setActiveId(null)
        if (!over) return
        const personId = active.data.current.personId
        const target = zoneToStatus(over.id)
        if (target === draft[personId]) return

        setNotice(null)
        setDraft((prev) => {
            const next = { ...prev }
            if (target === undefined) delete next[personId]
            else next[personId] = target
            return next
        })
    }

    // Save asks first when it would delete records; otherwise it saves straight away.
    function requestSave() {
        if (changed.length === 0 || saving) return
        if (removals.length > 0) setConfirming(true)
        else handleSave()
    }

    async function handleBackupFirst() {
        setBackupBusy(true)
        try {
            const fileName = await downloadBackup(serverUrl)
            setNotice({ type: 'ok', text: `Downloaded ${fileName}.` })
        } catch (err) {
            console.error('Downloading the backup failed:', err)
            setNotice({ type: 'error', text: describeError(err) })
        } finally {
            setBackupBusy(false)
        }
    }

    async function handleSave() {
        if (changed.length === 0) return
        setConfirming(false)
        // status null = remove the record (the person goes back to Not Recorded)
        const records = changed.map((p) => ({ person_id: p.id, status: draft[p.id] ?? null }))

        setSaving(true)
        setNotice(null)
        try {
            const response = await fetch(`${serverUrl}/api/attendance`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({ date: clock.date, records }),
            })
            const data = await readJson(response)
            setSaved((prev) => {
                const next = { ...prev }
                for (const r of records) {
                    if (r.status === null) delete next[r.person_id]
                    else next[r.person_id] = r.status
                }
                return next
            })
            setNotice({ type: 'ok', text: saveMessage(data) })
            if (onSaved) onSaved()
        } catch (err) {
            console.error('Saving attendance failed:', err)
            setNotice({ type: 'error', text: describeError(err) })
        } finally {
            setSaving(false)
        }
    }

    // Put the keyboard on the safe choice when the question appears.
    useEffect(() => {
        if (showConfirm) cancelRef.current?.focus()
    }, [showConfirm])

    if (clock?.weekend) {
        return (
            <div className="daily">
                <h1 className="daily-title">Today&apos;s Attendance</h1>
                <p className="message">{weekendMessage(clock.date)}</p>
            </div>
        )
    }

    return (
        <div className="daily">
            <h1 className="daily-title">Today&apos;s Attendance</h1>
            {clock && <p className="daily-date">{shortDate(clock.date)}</p>}

            <select
                className="daily-department"
                aria-label="Department"
                value={department}
                onChange={(e) => setDepartment(e.target.value)}
                disabled={departments.length === 0}
                title={departments.length === 0 ? 'No departments have been set up yet' : undefined}
            >
                <option value="">All departments</option>
                {departments.map((d) => (
                    <option key={d} value={d}>
                        {d}
                    </option>
                ))}
            </select>

            {loading ? (
                <p className="message">Loading…</p>
            ) : (
                clock && (
                    <DndContext
                        sensors={sensors}
                        onDragStart={({ active }) => setActiveId(active.id)}
                        onDragEnd={handleDragEnd}
                        onDragCancel={() => setActiveId(null)}
                    >
                        <div className="daily-board">
                            {STATUSES.map((col) => {
                                const members = visible.filter((p) => draft[p.id] === col.value)
                                return (
                                    <DropZone
                                        key={col.value}
                                        id={`zone-${col.value}`}
                                        className={`daily-column daily-column--${col.cls}`}
                                    >
                                        <h2 className="daily-column-title">
                                            {col.label} <span className="daily-count">{members.length}</span>
                                        </h2>
                                        <div className="daily-chips">
                                            {members.map((p) => (
                                                <PersonChip
                                                    key={p.id}
                                                    person={p}
                                                    status={draft[p.id]}
                                                    dirty={draft[p.id] !== saved[p.id]}
                                                />
                                            ))}
                                        </div>
                                    </DropZone>
                                )
                            })}
                        </div>

                        <DropZone id={TRAY_ID} className="daily-tray">
                            <h2 className="daily-tray-title">
                                Not Recorded <span className="daily-count">{unrecorded.length}</span>
                            </h2>
                            <div className="daily-chips">
                                {unrecorded.map((p) => (
                                    <PersonChip
                                        key={p.id}
                                        person={p}
                                        status={undefined}
                                        dirty={draft[p.id] !== saved[p.id]}
                                    />
                                ))}
                                {unrecorded.length === 0 && (
                                    <span className="daily-empty">Everyone is accounted for.</span>
                                )}
                            </div>
                        </DropZone>

                        <DragOverlay>
                            {activePerson ? (
                                <div className={`daily-chip daily-chip--${clsForStatus(draft[activePerson.id])} is-overlay`}>
                                    {activePerson.name}
                                </div>
                            ) : null}
                        </DragOverlay>
                    </DndContext>
                )
            )}

            {!loading && clock && showConfirm && (
                <div
                    className="daily-confirm"
                    role="alertdialog"
                    aria-labelledby="daily-confirm-title"
                    aria-describedby="daily-confirm-text"
                    onKeyDown={(e) => {
                        if (e.key === 'Escape') setConfirming(false)
                    }}
                >
                    <p id="daily-confirm-title" className="daily-confirm-title">
                        Remove today&apos;s attendance for these {countPeople(removals.length)} people: {removals.map((p) => p.name).join(', ')}?
                    </p>
                    <p id="daily-confirm-text" className="daily-confirm-text">
                        These records are deleted permanently and the
                        predictions are recalculated without them.
                    </p>
                    <div className="daily-confirm-actions">
                        <button
                            type="button"
                            className="daily-confirm-btn"
                            onClick={handleBackupFirst}
                            disabled={backupBusy || saving}
                        >
                            {backupBusy ? 'Preparing…' : 'Download backup first'}
                        </button>
                        <button
                            type="button"
                            className="daily-confirm-btn"
                            ref={cancelRef}
                            onClick={() => setConfirming(false)}
                            disabled={saving}
                        >
                            Cancel
                        </button>
                        <button
                            type="button"
                            className="daily-confirm-btn daily-confirm-btn--danger"
                            onClick={handleSave}
                            disabled={saving}
                        >
                            {saving ? 'Saving…' : 'Remove and save'}
                        </button>
                    </div>
                </div>
            )}

            {!loading && clock && !showConfirm && (
                <div className="daily-actions">
                    <button
                        type="button"
                        className="daily-save-btn"
                        onClick={requestSave}
                        disabled={saving || changed.length === 0}
                        aria-busy={saving}
                    >
                        {saving ? 'Saving…' : 'Save'}
                    </button>
                    {changed.length > 0 && (
                        <span className="daily-unsaved">
                            {changed.length} unsaved change{changed.length === 1 ? '' : 's'}
                        </span>
                    )}
                </div>
            )}

            {notice && (
                <p
                    className={`daily-notice daily-notice--${notice.type}`}
                    role={notice.type === 'error' ? 'alert' : 'status'}
                >
                    {notice.text}
                </p>
            )}

            {!loading && clock && (
                <AttendanceTally byStatus={tally.byStatus} notRecorded={tally.notRecorded} total={tally.total} />
            )}
        </div>
    )
}

// Admins get the drag-and-drop board. Everyone else gets their own status and a names-free tally.
function DailyAttendance({ serverUrl, isAdmin, onSaved }) {
    return isAdmin ? (
        <AdminBoard serverUrl={serverUrl} onSaved={onSaved} />
    ) : (
        <MyAttendance serverUrl={serverUrl} onSaved={onSaved} />
    )
}

export default DailyAttendance