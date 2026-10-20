// Shared by the Today's Attendance pages (admin board, user page and the tally).

// Codes match the backend: 0 = Out, 1 = In, 2 = Partial Day.
// Column order on the admin board, button order on the user page and the order of the tally all
// follow this list, so splitting Partial Day into AM and PM later starts here (and in
// ALLOWED_STATUSES in app.py).
export const STATUSES = [
    { value: 1, label: 'In', short: 'In', cls: 'in' },
    { value: 2, label: 'Partial Day', short: 'Partial', cls: 'partial-day' },
    { value: 0, label: 'Out', short: 'Out', cls: 'out' },
]

const DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
const MONTH_NAMES = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export function clsForStatus(status) {
    return STATUSES.find((s) => s.value === status)?.cls ?? 'none'
}

// 'Wed 14 Oct'. Built from the date parts so the browser's timezone cannot shift the day.
export function shortDate(iso) {
    const d = new Date(`${iso}T00:00:00`)
    return `${DAY_NAMES[d.getDay()].slice(0, 3)} ${d.getDate()} ${MONTH_NAMES[d.getMonth()]}`
}

export function weekendMessage(iso) {
    const dayName = DAY_NAMES[new Date(`${iso}T00:00:00`).getDay()]
    return `${shortDate(iso)} is a ${dayName}. No attendance on weekends.`
}

// '08:15' -> '8:15 AM'
export function formatCutoff(hhmm) {
    const [h, m] = hhmm.split(':').map(Number)
    return `${h % 12 || 12}:${String(m).padStart(2, '0')} ${h >= 12 ? 'PM' : 'AM'}`
}

export function describeError(err) {
    return err instanceof TypeError ? 'Could not reach the server.' : err.message
}

export async function downloadFromServer(url, fallbackName) {
    const response = await fetch(url, { credentials: 'include' })
    if (!response.ok) {
        let message = `HTTP ${response.status}`
        try {
            message = (await response.json()).error || message
        } catch {
            // not JSON: keep the status code
        }
        throw new Error(message)
    }
    const blob = await response.blob()
    const named = /filename="?([^";]+)"?/.exec(response.headers.get('Content-Disposition') || '')
    const fileName = named ? named[1] : fallbackName
    const objectUrl = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = objectUrl
    link.download = fileName
    document.body.appendChild(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
    return fileName
}

// Downloads the whole-database backup (people, departments, attendance) as a CSV file.
export function downloadBackup(serverUrl) {
    return downloadFromServer(`${serverUrl}/api/export/attendance.csv`, 'attendance-backup.csv')
}

// The 2 functions below help download a CSV of the new usernames and temporary passwords.
// Saves text as a file in the browser (UTF-8 with a byte-order mark, so Excel reads accents correctly).
export function downloadTextFile(fileName, text, type = 'text/csv') {
    const url = URL.createObjectURL(new Blob(['\uFEFF', text], { type: `${type};charset=utf-8` }))
    const link = document.createElement('a')
    link.href = url
    link.download = fileName
    document.body.appendChild(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
}

// One CSV cell: quoted when needed, and a leading = + - @ is neutralised so a spreadsheet never runs it as a formula.
export function csvCell(value) {
    let text = String(value ?? '')
    if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`
    return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text
}

export async function readJson(response) {
    let data = null
    try {
        data = await response.json()
    } catch {
        // Not JSON (for example an error page): the status code below says what went wrong.
    }
    if (!response.ok) throw new Error(data?.error || `HTTP ${response.status}`)
    if (data === null) throw new Error('Unexpected response from the server.')
    return data
}

/** True when `name` passes the search text (case-insensitive, anywhere in the name). */
export function matchesSearch(name, search) {
    const query = search.trim().toLowerCase()
    return !query || name.toLowerCase().includes(query)
}
