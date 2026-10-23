import { useState } from 'react'
import { downloadBackup } from './AttendanceUtils'
import './AttendanceForm.css' // shared look: .attendance-hint, .attendance-notice, .evaluate-btn
import './BackupSection.css'

// Backup: download everyone and all their attendance as one CSV (admin).
function BackupSection({ serverUrl }) {
    const [backupBusy, setBackupBusy] = useState(false)
    const [backupNotice, setBackupNotice] = useState(null)

    async function handleBackup() {
        setBackupBusy(true)
        setBackupNotice(null)
        try {
            const fileName = await downloadBackup(serverUrl)
            setBackupNotice({ type: 'ok', text: `Downloaded ${fileName}.` })
        } catch (err) {
            console.error('Downloading the backup failed:', err)
            setBackupNotice({
                type: 'error',
                text: err instanceof TypeError ? 'Could not reach the server.' : err.message,
            })
        } finally {
            setBackupBusy(false)
        }
    }

    return (
        <details name="admin-sections" className="backup-section">
            <summary>Backup</summary>
            <section className="attendance-backup">
                <p className="attendance-hint">
                    Download everyone and all their attendance as a CSV. Do this before removing attendance or
                    changing the database. Logins are not included.
                </p>
                <button type="button" className="evaluate-btn" onClick={handleBackup} disabled={backupBusy} aria-busy={backupBusy}>
                    {backupBusy ? 'Preparing…' : 'Download backup (CSV)'}
                </button>
                {backupNotice && (
                    <p
                        className={`attendance-notice attendance-notice--${backupNotice.type}`}
                        role={backupNotice.type === 'error' ? 'alert' : 'status'}
                    >
                        {backupNotice.text}
                    </p>
                )}
            </section>
        </details>
    )
}

export default BackupSection