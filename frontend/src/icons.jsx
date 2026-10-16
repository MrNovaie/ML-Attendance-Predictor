// Icons for the filter bar. They are drawn with currentColor, so they follow the text colour (light or dark).
// Funnel and calendar follow the two icons supplied for the project; the magnifier is a stand-in in the same style.

export function FilterIcon({ className }) {
    return (
        <svg className={className} viewBox="0 0 512 512" aria-hidden="true" focusable="false"
            fill="none" stroke="currentColor" strokeWidth="32" strokeLinejoin="round" strokeLinecap="round">
            <path d="M84 78 H428 Q462 78 442 108 L297 292 V386 L217 452 V292 L70 108 Q50 78 84 78 Z" />
        </svg>
    )
}

export function CalendarIcon({ className }) {
    return (
        <svg className={className} viewBox="0 0 512 512" aria-hidden="true" focusable="false" fill="currentColor">
            <defs>
                {/* cuts the round gap around where each ring meets the header */}
                <mask id="calendar-icon-rings">
                    <rect width="512" height="512" fill="#fff" />
                    <circle cx="140" cy="70" r="46" fill="#000" />
                    <circle cx="375" cy="70" r="46" fill="#000" />
                </mask>
            </defs>
            <g mask="url(#calendar-icon-rings)">
                {/* header band */}
                <path d="M88 62 H424 A46 46 0 0 1 470 108 V172 H42 V108 A46 46 0 0 1 88 62 Z" />
                {/* body with the six day squares cut out */}
                <path fillRule="evenodd"
                    d="M42 190 H470 V424 A46 46 0 0 1 424 470 H88 A46 46 0 0 1 42 424 Z
                       M125 252 h26 a18 18 0 0 1 18 18 v22 a18 18 0 0 1 -18 18 h-26 a18 18 0 0 1 -18 -18 v-22 a18 18 0 0 1 18 -18 Z
                       M242 252 h26 a18 18 0 0 1 18 18 v22 a18 18 0 0 1 -18 18 h-26 a18 18 0 0 1 -18 -18 v-22 a18 18 0 0 1 18 -18 Z
                       M359 252 h26 a18 18 0 0 1 18 18 v22 a18 18 0 0 1 -18 18 h-26 a18 18 0 0 1 -18 -18 v-22 a18 18 0 0 1 18 -18 Z
                       M125 360 h26 a18 18 0 0 1 18 18 v22 a18 18 0 0 1 -18 18 h-26 a18 18 0 0 1 -18 -18 v-22 a18 18 0 0 1 18 -18 Z
                       M242 360 h26 a18 18 0 0 1 18 18 v22 a18 18 0 0 1 -18 18 h-26 a18 18 0 0 1 -18 -18 v-22 a18 18 0 0 1 18 -18 Z
                       M359 360 h26 a18 18 0 0 1 18 18 v22 a18 18 0 0 1 -18 18 h-26 a18 18 0 0 1 -18 -18 v-22 a18 18 0 0 1 18 -18 Z" />
            </g>
            {/* the two rings */}
            <rect x="117" y="32" width="46" height="66" rx="23" />
            <rect x="352" y="32" width="46" height="66" rx="23" />
        </svg>
    )
}

export function SearchIcon({ className }) {
    return (
        <svg className={className} viewBox="0 0 512 512" aria-hidden="true" focusable="false"
            fill="none" stroke="currentColor" strokeWidth="32" strokeLinecap="round">
            <circle cx="214" cy="214" r="140" />
            <path d="M318 318 L444 444" />
        </svg>
    )
}
