"""Only authored messages may leave diagnostics for ordinary UI surfaces."""

PUBLIC_STATUSES = frozenset({
    '', 'Saved', 'Saving…', 'Working…', 'Completed', 'Web cache cleared',
    'Saved — backend restart required', 'Action could not be completed',
    'Use #RRGGBB', 'Choose a dark background to keep controls readable',
    'Choose a lighter text/accent color for readable contrast',
    'Choose an installed font', 'Text size must be 12–22',
    'Use one Ctrl/Alt shortcut', 'Shortcut conflicts with another Onyx action',
    'Shortcut conflicts with recording, editing or Windows',
    'Settings could not be saved; previous value retained.',
})


def public_status(value):
    """Never render arbitrary HTTP/provider/OS exception text as a status."""
    text = str(value or '')
    if text in PUBLIC_STATUSES:
        return text
    if text.startswith('Export: '):
        return 'Chat exported'
    return 'Action could not be completed'
