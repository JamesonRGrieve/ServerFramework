import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

from zephyrex.extensions.calendar.PRV_Calendar import AbstractCalendarProvider


class CalendlyProvider(AbstractCalendarProvider):
    """
    Calendly provider implementation for calendar scheduling.
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
        Initialize the Calendly provider with configuration parameters.
        """
        # Set default API URI if not provided
        if not api_uri:
            api_uri = "https://api.calendly.com"

        self.user_uri = kwargs.get("user_uri", "")

        super().__init__(
            api_key=api_key,
            api_uri=api_uri,
            access_token=access_token,
            timezone=timezone,
            extension_id=extension_id,
            **kwargs,
        )

        if not self.user_uri and access_token:
            # Try to get user URI from token. self.api_uri must be set
            # (by the super().__init__() call above) before this runs.
            self._get_user_info(access_token)

    def _get_user_info(self, token: str) -> None:
        """
        Get user information from Calendly API.
        """
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        try:
            response = requests.get(f"{self.api_uri}/users/me", headers=headers)
            if response.status_code == 200:
                user_data = response.json().get("resource", {})
                self.user_uri = user_data.get("uri", "")
                logging.info(f"Successfully got Calendly user URI: {self.user_uri}")
            else:
                logging.error(
                    f"Error getting Calendly user info: {response.status_code} - {response.text}"
                )
        except Exception as e:
            logging.error(f"Error getting Calendly user info: {str(e)}")

    def verify_token(self) -> None:
        """
        Verify Calendly authentication token.
        """
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        response = requests.get(f"{self.api_uri}/users/me", headers=headers)
        if response.status_code != 200:
            logging.error(
                f"Calendly token validation failed: {response.status_code} - {response.text}"
            )
            # Try to refresh token if available
            if (
                hasattr(self, "ApiClient")
                and self.ApiClient
                and hasattr(self.ApiClient, "refresh_oauth_token")
            ):
                self.access_token = self.ApiClient.refresh_oauth_token(
                    provider="calendly"
                )
                # Update user URI after token refresh
                self._get_user_info(self.access_token)

    def get_events(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        max_events: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Get Calendly scheduled events within a date range.
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

            # Get the scheduled events
            url = f"{self.api_uri}/scheduled_events"
            params = {
                "user": self.user_uri,
                "min_start_time": start_date.isoformat(),
                "max_start_time": end_date.isoformat(),
                "count": max_events,
            }

            response = requests.get(url, headers=headers, params=params)
            if response.status_code != 200:
                logging.error(
                    f"Error fetching Calendly events: {response.status_code} - {response.text}"
                )
                return []

            events_data = response.json().get("collection", [])
            formatted_events = []

            for event in events_data:
                # Get more details about the event
                event_uri = event.get("uri", "")
                if not event_uri:
                    continue

                # Get invitees for the event
                invitees_url = f"{self.api_uri}/scheduled_events/{event_uri.split('/')[-1]}/invitees"
                invitees_response = requests.get(invitees_url, headers=headers)
                invitees = []

                if invitees_response.status_code == 200:
                    invitees_data = invitees_response.json().get("collection", [])
                    invitees = [
                        invitee.get("email", "")
                        for invitee in invitees_data
                        if invitee.get("email")
                    ]

                event_data = {
                    "id": event_uri.split("/")[-1],
                    "subject": event.get("name", "Calendly Event"),
                    "start_time": event.get("start_time", ""),
                    "end_time": event.get("end_time", ""),
                    "organizer": self.user_uri,
                    "location": event.get("location", {}).get("name", ""),
                    "url": event.get("uri", ""),
                }

                if invitees:
                    event_data["attendees"] = invitees

                # Add to result list
                formatted_events.append(event_data)

            return formatted_events
        except Exception as e:
            logging.error(f"Error getting Calendly events: {str(e)}")
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
        Create a new Calendly ad-hoc event.
        Note: Calendly primarily works through scheduling links rather than direct event creation.
        This implements an ad-hoc invitations feature when available.
        """
        try:
            self.verify_token()
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            # Get available event types first
            event_types_url = f"{self.api_uri}/event_types"
            params = {"user": self.user_uri}

            response = requests.get(event_types_url, headers=headers, params=params)
            if response.status_code != 200:
                logging.error(
                    f"Error fetching Calendly event types: {response.status_code} - {response.text}"
                )
                return {
                    "success": False,
                    "message": "Cannot create Calendly event: Unable to fetch event types.",
                }

            event_types = response.json().get("collection", [])
            if not event_types:
                return {
                    "success": False,
                    "message": "No event types found in your Calendly account.",
                }

            # Select the first event type as default
            event_type_uri = event_types[0].get("uri", "")

            # Create an ad-hoc invitation
            # Note: This feature is only available on certain Calendly plans
            ad_hoc_url = f"{self.api_uri}/invitations"

            invite_data = {
                "event_type": event_type_uri,
                "invitees": [],
                "suggested_times": [
                    {
                        "start_time": start_time.isoformat(),
                        "end_time": end_time.isoformat(),
                    }
                ],
                "name": subject,
            }

            if attendees:
                for email in attendees:
                    invite_data["invitees"].append({"email": email})

            if description:
                invite_data["message"] = description

            if location:
                invite_data["location"] = {"name": location}

            # Note: Calendly API for direct event creation is limited
            # This approach uses the invitations API which is a premium feature
            response = requests.post(ad_hoc_url, headers=headers, json=invite_data)

            if response.status_code != 201 and response.status_code != 200:
                # If direct creation fails, return scheduling link instead
                return {
                    "success": False,
                    "message": "Direct event creation is not available with your Calendly plan. Use scheduling links instead.",
                    "scheduling_link": event_types[0].get("scheduling_url", ""),
                }

            invitation = response.json().get("resource", {})
            return {
                "success": True,
                "message": "Calendly invitation created successfully.",
                "event_id": invitation.get("uri", "").split("/")[-1],
                "event_link": invitation.get("booking_url", ""),
            }

        except Exception as e:
            logging.error(f"Error creating Calendly event: {str(e)}")
            return {
                "success": False,
                "message": f"Failed to create Calendly event: {str(e)}",
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
        Update a Calendly event.
        Note: Calendly has limited API capabilities for updating events.
        """
        try:
            self.verify_token()
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            # Currently, Calendly API has limited update capabilities
            # We can only cancel and reschedule

            # If we want to reschedule
            if start_time and end_time:
                # Cancellation first
                cancel_url = f"{self.api_uri}/scheduled_events/{event_id}/cancellation"
                cancel_data = {"reason": "Rescheduling"}

                cancel_response = requests.post(
                    cancel_url, headers=headers, json=cancel_data
                )
                if (
                    cancel_response.status_code != 201
                    and cancel_response.status_code != 200
                ):
                    logging.error(
                        f"Error canceling Calendly event: {cancel_response.status_code} - {cancel_response.text}"
                    )
                    return {
                        "success": False,
                        "message": "Cannot update Calendly event: Failed to cancel existing event.",
                    }

                # Now create a new event
                create_result = self.create_event(
                    subject=subject or "Calendly Event",
                    start_time=start_time,
                    end_time=end_time,
                    location=location,
                    attendees=attendees,
                    description=description,
                )

                if create_result.get("success"):
                    return {
                        "success": True,
                        "message": "Calendly event rescheduled successfully.",
                        "event_id": create_result.get("event_id"),
                    }
                else:
                    return {
                        "success": False,
                        "message": "Failed to reschedule Calendly event. "
                        + create_result.get("message", ""),
                    }

            # For other updates, we need to inform the user of limitations
            return {
                "success": False,
                "message": "Calendly API has limited update capabilities. Only rescheduling (with new start/end times) is supported.",
            }

        except Exception as e:
            logging.error(f"Error updating Calendly event: {str(e)}")
            return {
                "success": False,
                "message": f"Failed to update Calendly event: {str(e)}",
            }

    def delete_event(self, event_id: str) -> str:
        """
        Delete (cancel) a Calendly event.
        """
        try:
            self.verify_token()
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            # In Calendly, we cancel events rather than delete them
            cancel_url = f"{self.api_uri}/scheduled_events/{event_id}/cancellation"
            cancel_data = {"reason": "Canceled via API"}

            response = requests.post(cancel_url, headers=headers, json=cancel_data)
            if response.status_code != 201 and response.status_code != 200:
                logging.error(
                    f"Error canceling Calendly event: {response.status_code} - {response.text}"
                )
                return f"Failed to cancel Calendly event: {response.status_code}: {response.text}"

            return "Calendly event canceled successfully."
        except Exception as e:
            logging.error(f"Error canceling Calendly event: {str(e)}")
            return f"Failed to cancel Calendly event: {str(e)}"

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
        Find available time slots in Calendly.
        This leverages Calendly's available_times API.
        """
        try:
            self.verify_token()
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            # Get event types first
            event_types_url = f"{self.api_uri}/event_types"
            params = {"user": self.user_uri}

            response = requests.get(event_types_url, headers=headers, params=params)
            if response.status_code != 200:
                logging.error(
                    f"Error fetching Calendly event types: {response.status_code} - {response.text}"
                )
                return []

            event_types = response.json().get("collection", [])
            if not event_types:
                logging.error("No event types found in Calendly account")
                return []

            # Find an event type that matches our duration
            matched_event_type = None
            for event_type in event_types:
                if event_type.get("duration") == duration_minutes:
                    matched_event_type = event_type
                    break

            # If no exact match, use the first event type
            if not matched_event_type and event_types:
                matched_event_type = event_types[0]

            if not matched_event_type:
                logging.error("Could not find a suitable event type in Calendly")
                return []

            event_type_uri = matched_event_type.get("uri", "")

            # Get available times for this event type
            end_date = start_date + timedelta(days=num_days)
            available_times_url = f"{self.api_uri}/event_type_available_times"
            available_times_params = {
                "event_type": event_type_uri,
                "start_time": start_date.isoformat(),
                "end_time": end_date.isoformat(),
            }

            times_response = requests.get(
                available_times_url, headers=headers, params=available_times_params
            )
            if times_response.status_code != 200:
                logging.error(
                    f"Error fetching Calendly available times: {times_response.status_code} - {times_response.text}"
                )
                return []

            available_times_data = times_response.json().get("collection", [])

            # Format the response
            available_slots = []
            for time_slot in available_times_data:
                start_time = time_slot.get("start_time")
                status = time_slot.get("status")

                # Only include available slots
                if status == "available" and start_time:
                    # Calculate end time based on event type duration
                    start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                    end_dt = start_dt + timedelta(
                        minutes=matched_event_type.get("duration", duration_minutes)
                    )

                    # Add slot to result
                    available_slots.append(
                        {
                            "start_time": start_time,
                            "end_time": end_dt.isoformat(),
                        }
                    )

            return available_slots
        except Exception as e:
            logging.error(f"Error finding available timeslots in Calendly: {str(e)}")
            return []

    def check_time_availability(
        self, start_time: datetime, end_time: datetime
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Check if a specific time slot is available in Calendly.
        """
        try:
            self.verify_token()
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            # Get scheduled events for that time period
            scheduled_url = f"{self.api_uri}/scheduled_events"
            params = {
                "user": self.user_uri,
                "min_start_time": start_time.isoformat(),
                "max_start_time": end_time.isoformat(),
            }

            response = requests.get(scheduled_url, headers=headers, params=params)
            if response.status_code != 200:
                logging.error(
                    f"Error checking Calendly availability: {response.status_code} - {response.text}"
                )
                raise Exception(
                    f"Failed to check Calendly availability: {response.status_code}: {response.text}"
                )

            events = response.json().get("collection", [])

            # If there are events in this time slot, it's not available
            if events:
                event = events[0]
                return False, {
                    "id": event.get("uri", "").split("/")[-1],
                    "subject": event.get("name", "Busy"),
                    "start_time": event.get("start_time", ""),
                    "end_time": event.get("end_time", ""),
                }

            # Time slot is available
            return True, None
        except Exception as e:
            logging.error(f"Error checking time availability in Calendly: {str(e)}")
            raise

    def get_platform_name(self) -> str:
        """
        Get the name of the calendar platform this provider interacts with.
        """
        return "Calendly"
