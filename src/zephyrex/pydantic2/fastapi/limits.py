# SPDX-License-Identifier: AGPL-3.0-or-later
"""Size bounds every generated REST route enforces.

One home for them so LIST, SEARCH and the batch routes cannot drift apart:
a page is at most ``MAX_PAGE_SIZE`` records, a batch write at most
``MAX_BATCH_SIZE`` items.
"""

# The most records one LIST or SEARCH page returns (``limit``/``page_size``).
MAX_PAGE_SIZE = 1000

# The most items one batch create, update or delete names. Each item is a
# separate write in the request, so this bounds the work one call can start.
MAX_BATCH_SIZE = 100
