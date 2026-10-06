# SPDX-License-Identifier: AGPL-3.0-or-later
"""What the email tests share: real provider instances, of any scope and
owner, with settings, written the way their owner would write them."""

from zephyrex.testing.factories import provider_instance_as

# A new instance of an email provider (``provider_instance_as``).
email_instance = provider_instance_as
