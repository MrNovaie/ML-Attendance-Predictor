import { CalendarIcon, FilterIcon, SearchIcon } from './icons'
import './FilterBar.css'

/**
 * Department filter, optional date picker and name search, in one row.
 *
 * PC:    [ Department v ]  [ date ]  [ Search by name ........ ]
 * Phone: [ Search by name ........ ] [calendar] [filter]   (the two controls become square icon buttons;
 *        the real <select> and <input type="date"> sit invisibly on top of them, so a tap opens the native picker)
 *
 * Leave out `date` / `onDateChange` where there is no calendar (the Today's Attendance board).
 */
function FilterBar({ departments, department, onDepartmentChange, search, onSearchChange, date, onDateChange }) {
    return (
        <div className="filter-bar" role="search">
            <div className="filter-square filter-square--department" data-active={department ? 'true' : 'false'}>
                <FilterIcon className="filter-icon" />
                <select
                    className="filter-control"
                    aria-label="Department"
                    value={department}
                    onChange={(e) => onDepartmentChange(e.target.value)}
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
            </div>

            {onDateChange && (
                <div className="filter-square filter-square--date">
                    <CalendarIcon className="filter-icon" />
                    <input
                        className="filter-control"
                        type="date"
                        aria-label="Select a date"
                        value={date}
                        onChange={(e) => onDateChange(e.target.value)}
                    />
                </div>
            )}

            <div className="filter-search">
                <SearchIcon className="filter-search-icon" />
                <input
                    type="search"
                    aria-label="Search by name"
                    placeholder="Search by name"
                    autoComplete="off"
                    value={search}
                    onChange={(e) => onSearchChange(e.target.value)}
                />
            </div>
        </div>
    )
}

export default FilterBar