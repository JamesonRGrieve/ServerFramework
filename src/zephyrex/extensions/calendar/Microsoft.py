import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

from zephyrex.extensions.calendar.PRV_Calendar import AbstractCalendarProvider


class MicrosoftCalendarProvider(AbstractCalendarProvider):
    """
    Microsoft Calendar provider implementation.
    """

    def __init__(
        self,
        api_key: str = "",
        api_uri: str = "",
        access_token: str = "",
        timezone: str = "UTC",
        extension_id: Optional[str] = None,
        **kwargs,
    ):
        """
        Initialize the Microsoft Calendar provider with configuration parameters.
        """
        # Set default API URI if not provided
        if not api_uri:
            api_uri = "https://graph.microsoft.com/v1.0"
        super().__init__(
            api_key=api_key,
            api_uri=api_uri,
            access_token=access_token,
            timezone=timezone,
            extension_id=extension_id,
            **kwargs,
        )

    def verify_token(self) -> None:
        """
        Verify and refresh the Microsoft OAuth token if necessary.
        """
        # Check if we can refresh the token via our auth mechanism
        if (
            hasattr(self, "ApiClient")
            and self.ApiClient
            and hasattr(self.ApiClient, "refresh_oauth_token")
        ):
            self.access_token = self.ApiClient.refresh_oauth_token(provider="microsoft")

        # Verify token is valid by making a test call
        headers = {"Authorization": f"Bearer {self.access_token}"}
        response = requests.get(f"{self.api_uri}/me", headers=headers)
        if response.status_code != 200:
            logging.error(
                f"Microsoft access token validation failed: {response.status_code} - {response.text}"
            )

    def get_events(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        max_events: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Get Microsoft Calendar events within a date range.
        """
        try:
            self.verify_token()
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            # Set default date range if not provided
            if start_date is None:
                start_date = datetime.now()
            if end_date is None:
                end_date = start_date + timedelta(days=7)

            # Format the request URL for calendar view
            url = (
                f"{self.api_uri}/me/calendar/calendarView?"
                f"startDateTime={start_date.isoformat()}Z&"
                f"endDateTime={end_date.isoformat()}Z&"
                f"$top={max_events}&$orderby=start/dateTime"
            )

            response = requests.get(url, headers=headers)
            if response.status_code != 200:
                logging.error(
                    f"Error fetching Microsoft Calendar events: {response.status_code} - {response.text}"
                )
                return []

            events_data = response.json().get("value", [])
            formatted_events = []

            for event in events_data:
                event_data = {
                    "id": event["id"],
                    "subject": event.get("subject", "No Subject"),
                    "start_time": event["start"]["dateTime"],
                    "end_time": event["end"]["dateTime"],
                    "organizer": event["organizer"]["emailAddress"]["address"],
                }

                # Add optional fields if present
                if "location" in event and "displayName" in event["location"]:
                    event_data["location"] = event["location"]["displayName"]
                if "bodyPreview" in event:
                    event_data["description"] = event["bodyPreview"]
                if "isOnlineMeeting" in event and event["isOnlineMeeting"]:
                    event_data["is_online_meeting"] = True
                    if "onlineMeeting" in event and "joinUrl" in event["onlineMeeting"]:
                        event_data["meeting_url"] = event["onlineMeeting"]["joinUrl"]
                if "attendees" in event:
                    event_data["attendees"] = [
                        attendee["emailAddress"]["address"]
                        for attendee in event["attendees"]
                    ]

                formatted_events.append(event_data)

            return formatted_events
        except Exception as e:
            logging.error(f"Error getting Microsoft Calendar events: {str(e)}")
            return []

    def create_event(
        self,
        subject: str,
        start_time: datetime,
        end_time: datetime,
        location: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        description: Optional[str] = None,
        is_online_meeting: bool = False,
    ) -> Dict[str, Any]:
        """
        Create a new Microsoft Calendar event.
        """
        try:
            # First check if the time is available
            is_available, conflict = self.check_time_availability(
                start_time, end_time
            )
            if not is_available:
                return {
                    "success": False,
                    "message": f"The requested time slot conflicts with another event: '{conflict['subject']}'. Please choose a different time.",
                }

            self.verify_token()
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            event_data = {
                "subject": subject,
                "start": {
                    "dateTime": start_time.isoformat(),
                    "timeZone": self.timezone,
                },
                "end": {
                    "dateTime": end_time.isoformat(),
                    "timeZone": self.timezone,
                },
                "isOnlineMeeting": is_online_meeting,
                "reminderMinutesBeforeStart": 15,  # Default reminder
            }

            # Add optional fields if provided
            if location:
                event_data["location"] = {"displayName": location}
            if description:
                event_data["body"] = {"contentType": "HTML", "content": description}
            if attendees:
                event_data["attendees"] = [
                    {"emailAddress": {"address": email}, "type": "required"}
                    for email in attendees
                ]

            response = requests.post(
                f"{self.api_uri}/me/events", headers=headers, json=event_data
            )

            if response.status_code != 201:
                logging.error(
                    f"Error creating Microsoft Calendar event: {response.status_code} - {response.text}"
                )
                return {
                    "success": False,
                    "message": f"Failed to create event: {response.status_code}: {response.text}",
                }

            created_event = response.json()
            return {
                "success": True,
                "message": "Calendar event created successfully.",
                "event_id": created_event["id"],
                "event_link": created_event.get("webLink", ""),
            }
        except Exception as e:
            logging.error(f"Error creating Microsoft Calendar event: {str(e)}")
            return {
                "success": False,
                "message": f"Failed to create calendar event: {str(e)}",
            }

    def update_event(
        self,
        event_id: str,
        subject: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        location: Optional[str] = None,
        attendees: Optional[List[str]] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Update an existing Microsoft Calendar event.
        """
        try:
            self.verify_token()
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            # Check for conflicts if changing time
            if start_time and end_time:
                is_available, conflict = self.check_time_availability(
                    start_time, end_time
                )
                # If conflict is with an event other than the one being updated
                if not is_available and conflict.get("id") != event_id:
                    return {
                        "success": False,
                        "message": f"The requested time slot conflicts with another event: '{conflict['subject']}'. Please choose a different time.",
                    }

            # Build update data with only provided fields
            update_data = {}
            if subject:
                update_data["subject"] = subject
            if start_time:
                update_data["start"] = {
                    "dateTime": start_time.isoformat(),
                    "timeZone": self.timezone,
                }
            if end_time:
                update_data["end"] = {
                    "dateTime": end_time.isoformat(),
                    "timeZone": self.timezone,
                }
            if location is not None:  # Allow clearing the location
                update_data["location"] = {"displayName": location}
            if description is not None:  # Allow clearing the description
                update_data["body"] = {"contentType": "HTML", "content": description}
            if attendees is not None:  # Allow clearing attendees
                update_data["attendees"] = [
                    {"emailAddress": {"address": email}, "type": "required"}
                    for email in attendees
                ]

            # Update the event
            response = requests.patch(
                f"{self.api_uri}/me/events/{event_id}",
                headers=headers,
                json=update_data,
            )

            if response.status_code != 200:
                logging.error(
                    f"Error updating Microsoft Calendar event: {response.status_code} - {response.text}"
                )
                return {
                    "success": False,
                    "message": f"Failed to update event: {response.status_code}: {response.text}",
                }

            return {
                "success": True,
                "message": "Calendar event updated successfully.",
                "event_id": event_id,
            }
        except Exception as e:
            logging.error(f"Error updating Microsoft Calendar event: {str(e)}")
            return {
                "success": False,
                "message": f"Failed to update calendar event: {str(e)}",
            }

    def delete_event(self, event_id: str) -> str:
        """
        Delete a Microsoft Calendar event.
        """
        try:
            self.verify_token()
            headers = {"Authorization": f"Bearer {self.access_token}"}

            # Delete the event
            response = requests.delete(
                f"{self.api_uri}/me/events/{event_id}", headers=headers
            )

            if response.status_code != 204:
                logging.error(
                    f"Error deleting Microsoft Calendar event: {response.status_code} - {response.text}"
                )
                return (
                    f"Failed to delete event: {response.status_code}: {response.text}"
                )

            return "Calendar event deleted successfully."
        except Exception as e:
            logging.error(f"Error deleting Microsoft Calendar event: {str(e)}")
            return f"Failed to delete calendar event: {str(e)}"

    def get_available_timeslots(
        self,
        start_date: datetime,
        num_days: int = 7,
        work_day_start: str = "09:00",
        work_day_end: str = "17:00",
        duration_minutes: int = 30,
        buffer_minutes: int = 0,
    ) -> List[Dict[str, str]]:
        """
        Find available time slots over a specified number of days in Microsoft Calendar.
        """
        try:
            self.verify_token()
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            end_date = start_date + timedelta(days=num_days)
            end_search_date = end_date.date()

            # Get all events for the date range
            url = (
                f"{self.api_uri}/me/calendar/calendarView?"
                f"startDateTime={start_date.isoformat()}Z&"
                f"endDateTime={end_date.isoformat()}Z&"
                f"$orderby=start/dateTime"
            )

            response = requests.get(url, headers=headers)
            if response.status_code != 200:
                logging.error(
                    f"Error fetching Microsoft Calendar events: {response.status_code} - {response.text}"
                )
                return []

            events = response.json().get("value", [])

            # Set up workday time boundaries
            day_start = datetime.strptime(work_day_start, "%H:%M").time()
            day_end = datetime.strptime(work_day_end, "%H:%M").time()

            # Find available slots
            available_slots = []
            current_date = start_date.date()

            while current_date < end_search_date:
                # Create datetime objects for current day start and end
                current_day_start = datetime.combine(current_date, day_start)
                current_day_end = datetime.combine(current_date, day_end)

                # Filter events for the current day
                day_events = []
                for event in events:
                    if event.get("isAllDay", False):
                        continue

                    event_start = datetime.fromisoformat(
                        event["start"]["dateTime"].replace("Z", "+00:00")
                    )
                    event_end = datetime.fromisoformat(
                        event["end"]["dateTime"].replace("Z", "+00:00")
                    )

                    # Check if this event is on the current day
                    if (
                        event_start.date() == current_date
                        or event_end.date() == current_date
                    ):
                        day_events.append((event_start, event_end))

                # Sort events by start time
                day_events.sort(key=lambda x: x[0])

                # Start at beginning of workday
                slot_start = current_day_start

                # Find free slots
                for event_start, event_end in day_events:
                    # Create a potential slot before the current event if there's enough time
                    if slot_start < event_start:
                        # Make sure the slot fits before the event
                        slot_end = slot_start + timedelta(minutes=duration_minutes)

                        # If the slot fits before the event, add it
                        if (
                            slot_end <= event_start
                            and slot_start >= current_day_start
                            and slot_end <= current_day_end
                        ):
                            available_slots.append(
                                {
                                    "start_time": slot_start.isoformat(),
                                    "end_time": slot_end.isoformat(),
                                }
                            )

                            # Move to next slot with buffer
                            slot_start += timedelta(
                                minutes=(duration_minutes + buffer_minutes)
                            )
                            slot_end = slot_start + timedelta(minutes=duration_minutes)

                    # Move slot_start to after the current event (with buffer)
                    slot_start = max(
                        slot_start, event_end + timedelta(minutes=buffer_minutes)
                    )

                # Add any remaining slots at the end of the day
                while slot_start < current_day_end:
                    slot_end = slot_start + timedelta(minutes=duration_minutes)
                    if slot_end <= current_day_end:
                        available_slots.append(
                            {
                                "start_time": slot_start.isoformat(),
                                "end_time": slot_end.isoformat(),
                            }
                        )

                        # Move to next slot with buffer
                        slot_start += timedelta(
                            minutes=duration_minutes + buffer_minutes
                        )
                    else:
                        break

                # Move to next day
                current_date += timedelta(days=1)

            return available_slots
        except Exception as e:
            logging.error(
                f"Error finding available timeslots in Microsoft Calendar: {str(e)}"
            )
            return []

    def check_time_availability(
        self, start_time: datetime, end_time: datetime
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Check if a specific time slot is available.
        """
        try:
            self.verify_token()
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            # Format the request URL for calendar view
            url = (
                f"{self.api_uri}/me/calendar/calendarView?"
                f"startDateTime={start_time.isoformat()}Z&"
                f"endDateTime={end_time.isoformat()}Z"
            )

            response = requests.get(url, headers=headers)
            if response.status_code != 200:
                logging.error(
                    f"Error checking time availability in Microsoft Calendar: {response.status_code} - {response.text}"
                )
                raise Exception(
                    f"Failed to check calendar availability: {response.status_code}: {response.text}"
                )

            events = response.json().get("value", [])

            # If there are no events, the time is available
            if not events:
                return True, None

            # If there are events, find the first conflict
            for event in events:
                # Skip events marked as "free"
                if event.get("showAs", "").lower() == "free":
                    continue

                # Return the conflicting event
                return False, {
                    "id": event["id"],
                    "subject": event.get("subject", "Busy"),
                    "start_time": event["start"]["dateTime"],
                    "end_time": event["end"]["dateTime"],
                }

            # If all events are marked as "free", time is available
            return True, None
        except Exception as e:
            logging.error(
                f"Error checking time availability in Microsoft Calendar: {str(e)}"
            )
            raise

    def get_platform_name(self) -> str:
        """
        Get the name of the calendar platform this provider interacts with.
        """
        return "Microsoft 365 Calendar"
