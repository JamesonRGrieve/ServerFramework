import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

from zephyrex.extensions.calendar.PRV_Calendar import AbstractCalendarProvider


class GitHubProjectsProvider(AbstractCalendarProvider):
    """
    GitHub Projects provider implementation for calendar-like functionality.
    Uses GitHub Projects API to manage tasks with due dates as calendar-like events.
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
        Initialize the GitHub Projects provider with configuration parameters.
        """
        # Set default API URI if not provided
        if not api_uri:
            api_uri = "https://api.github.com"

        self.github_username = kwargs.get("github_username", "")
        self.repository = kwargs.get("repository", "")
        self.project_number = kwargs.get("project_number")

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
        Verify GitHub authentication token.
        """
        headers = {
            "Authorization": f"token {self.access_token}",
            "Accept": "application/vnd.github.v3+json",
        }
        response = requests.get(f"{self.api_uri}/user", headers=headers)
        if response.status_code != 200:
            logging.error(
                f"GitHub token validation failed: {response.status_code} - {response.text}"
            )

    def get_events(
        self,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        max_events: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Get GitHub Project items with due dates as calendar events.
        """
        try:
            self.verify_token()
            headers = {
                "Authorization": f"token {self.access_token}",
                "Accept": "application/vnd.github.v3+json",
            }

            # Set default date range if not provided
            if start_date is None:
                start_date = datetime.now()
            if end_date is None:
                end_date = start_date + timedelta(days=30)

            # First, we need to query the project and get items with due dates
            # For GitHub Projects API v2 (GraphQL)
            graphql_url = f"{self.api_uri}/graphql"
            graphql_headers = {
                "Authorization": f"token {self.access_token}",
                "Content-Type": "application/json",
            }

            # Get the project ID first
            project_query = {
                "query": f"""
                query {{
                  user(login: "{self.github_username}") {{
                    projectV2(number: {self.project_number}) {{
                      id
                    }}
                  }}
                }}
                """
            }

            project_response = requests.post(
                graphql_url, headers=graphql_headers, json=project_query
            )
            if project_response.status_code != 200:
                logging.error(
                    f"Error fetching GitHub project: {project_response.status_code} - {project_response.text}"
                )
                return []

            project_data = project_response.json()
            project_id = (
                project_data.get("data", {})
                .get("user", {})
                .get("projectV2", {})
                .get("id")
            )

            if not project_id:
                logging.error("Could not find GitHub project ID")
                return []

            # Now get items with due dates
            items_query = {
                "query": f"""
                query {{
                  node(id: "{project_id}") {{
                    ... on ProjectV2 {{
                      items(first: {max_events}) {{
                        nodes {{
                          id
                          content {{
                            ... on Issue {{
                              id
                              title
                              body
                              createdAt
                              assignees(first: 5) {{
                                nodes {{
                                  login
                                }}
                              }}
                            }}
                            ... on PullRequest {{
                              id
                              title
                              body
                              createdAt
                              assignees(first: 5) {{
                                nodes {{
                                  login
                                }}
                              }}
                            }}
                            ... on DraftIssue {{
                              id
                              title
                              body
                              createdAt
                              assignees {{
                                nodes {{
                                  login
                                }}
                              }}
                            }}
                          }}
                          fieldValues(first: 10) {{
                            nodes {{
                              ... on ProjectV2ItemFieldDateValue {{
                                date
                                field {{
                                  ... on ProjectV2FieldCommon {{
                                    name
                                  }}
                                }}
                              }}
                            }}
                          }}
                        }}
                      }}
                    }}
                  }}
                }}
                """
            }

            items_response = requests.post(
                graphql_url, headers=graphql_headers, json=items_query
            )
            if items_response.status_code != 200:
                logging.error(
                    f"Error fetching GitHub project items: {items_response.status_code} - {items_response.text}"
                )
                return []

            items_data = items_response.json()
            item_nodes = (
                items_data.get("data", {})
                .get("node", {})
                .get("items", {})
                .get("nodes", [])
            )

            # Filter items by due date field within our date range
            formatted_events = []

            for item in item_nodes:
                content = item.get("content", {})
                field_values = item.get("fieldValues", {}).get("nodes", [])

                # Find due date field
                due_date = None
                for field_value in field_values:
                    if field_value.get("field", {}).get("name", "").lower() in [
                        "due date",
                        "due",
                        "date",
                    ]:
                        due_date = field_value.get("date")
                        break

                if not due_date:
                    continue

                # Convert to datetime
                try:
                    due_date_dt = datetime.fromisoformat(
                        due_date.replace("Z", "+00:00")
                    )

                    # Check if within range
                    if due_date_dt < start_date or due_date_dt > end_date:
                        continue

                    # Get assignees
                    assignees = []
                    if "assignees" in content:
                        assignee_nodes = content.get("assignees", {}).get("nodes", [])
                        assignees = [node.get("login") for node in assignee_nodes]

                    # Format event
                    event_data = {
                        "id": item.get("id"),
                        "subject": content.get("title", "No Title"),
                        "start_time": due_date,
                        "end_time": due_date,  # GitHub doesn't have end time, just due date
                        "description": content.get("body", ""),
                        "organizer": self.github_username,
                    }

                    if assignees:
                        event_data["attendees"] = assignees

                    formatted_events.append(event_data)
                except (ValueError, TypeError):
                    continue

            # Sort by due date
            formatted_events.sort(key=lambda x: x["start_time"])

            return formatted_events
        except Exception as e:
            logging.error(f"Error getting GitHub Projects events: {str(e)}")
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
        Create a new GitHub Project item with a due date (as a calendar event).
        """
        try:
            self.verify_token()
            graphql_url = f"{self.api_uri}/graphql"
            graphql_headers = {
                "Authorization": f"token {self.access_token}",
                "Content-Type": "application/json",
            }

            # Get the project ID first
            project_query = {
                "query": f"""
                query {{
                  user(login: "{self.github_username}") {{
                    projectV2(number: {self.project_number}) {{
                      id
                    }}
                  }}
                }}
                """
            }

            project_response = requests.post(
                graphql_url, headers=graphql_headers, json=project_query
            )
            if project_response.status_code != 200:
                logging.error(
                    f"Error fetching GitHub project: {project_response.status_code} - {project_response.text}"
                )
                return {
                    "success": False,
                    "message": f"Failed to find GitHub project: {project_response.status_code}: {project_response.text}",
                }

            project_data = project_response.json()
            project_id = (
                project_data.get("data", {})
                .get("user", {})
                .get("projectV2", {})
                .get("id")
            )

            if not project_id:
                return {
                    "success": False,
                    "message": "Could not find GitHub project ID",
                }

            # Create a draft issue
            create_draft_query = {
                "query": f"""
                mutation {{
                  addProjectV2DraftIssue(input: {{
                    projectId: "{project_id}"
                    title: "{subject}"
                    body: "{description or ''}"
                  }}) {{
                    projectItem {{
                      id
                    }}
                  }}
                }}
                """
            }

            draft_response = requests.post(
                graphql_url, headers=graphql_headers, json=create_draft_query
            )
            if draft_response.status_code != 200:
                logging.error(
                    f"Error creating GitHub draft issue: {draft_response.status_code} - {draft_response.text}"
                )
                return {
                    "success": False,
                    "message": f"Failed to create GitHub draft issue: {draft_response.status_code}: {draft_response.text}",
                }

            draft_data = draft_response.json()
            item_id = (
                draft_data.get("data", {})
                .get("addProjectV2DraftIssue", {})
                .get("projectItem", {})
                .get("id")
            )

            if not item_id:
                return {
                    "success": False,
                    "message": "Could not create GitHub project item",
                }

            # Get the date field ID
            fields_query = {
                "query": f"""
                query {{
                  node(id: "{project_id}") {{
                    ... on ProjectV2 {{
                      fields(first: 20) {{
                        nodes {{
                          ... on ProjectV2Field {{
                            id
                            name
                          }}
                        }}
                      }}
                    }}
                  }}
                }}
                """
            }

            fields_response = requests.post(
                graphql_url, headers=graphql_headers, json=fields_query
            )
            fields_data = fields_response.json()

            date_field_id = None
            fields = (
                fields_data.get("data", {})
                .get("node", {})
                .get("fields", {})
                .get("nodes", [])
            )

            for field in fields:
                if field.get("name", "").lower() in ["due date", "due", "date"]:
                    date_field_id = field.get("id")
                    break

            if not date_field_id:
                # Try to create a date field
                create_field_query = {
                    "query": f"""
                    mutation {{
                      createProjectV2Field(input: {{
                        projectId: "{project_id}"
                        dataType: DATE
                        name: "Due Date"
                      }}) {{
                        projectField {{
                          id
                        }}
                      }}
                    }}
                    """
                }

                field_response = requests.post(
                    graphql_url, headers=graphql_headers, json=create_field_query
                )
                field_data = field_response.json()
                date_field_id = (
                    field_data.get("data", {})
                    .get("createProjectV2Field", {})
                    .get("projectField", {})
                    .get("id")
                )

                if not date_field_id:
                    return {
                        "success": False,
                        "message": "Could not find or create due date field in GitHub project",
                    }

            # Set the due date
            due_date = start_time.date().isoformat()
            update_field_query = {
                "query": f"""
                mutation {{
                  updateProjectV2ItemFieldValue(input: {{
                    projectId: "{project_id}"
                    itemId: "{item_id}"
                    fieldId: "{date_field_id}"
                    value: {{
                      date: "{due_date}"
                    }}
                  }}) {{
                    projectV2Item {{
                      id
                    }}
                  }}
                }}
                """
            }

            update_response = requests.post(
                graphql_url, headers=graphql_headers, json=update_field_query
            )
            if update_response.status_code != 200:
                logging.error(
                    f"Error setting GitHub item due date: {update_response.status_code} - {update_response.text}"
                )
                return {
                    "success": False,
                    "message": f"Failed to set due date on GitHub project item: {update_response.status_code}: {update_response.text}",
                }

            # Set assignees if provided
            # Note: In Projects v2, assigning users to draft issues is complex
            # Would need to convert to full issue first

            return {
                "success": True,
                "message": "GitHub project item created successfully with due date.",
                "event_id": item_id,
                "event_link": f"https://github.com/{self.github_username}/{self.repository}/projects/{self.project_number}",
            }
        except Exception as e:
            logging.error(f"Error creating GitHub project item: {str(e)}")
            return {
                "success": False,
                "message": f"Failed to create GitHub project item: {str(e)}",
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
        Update a GitHub Project item (representing a calendar event).
        """
        try:
            self.verify_token()
            graphql_url = f"{self.api_uri}/graphql"
            graphql_headers = {
                "Authorization": f"token {self.access_token}",
                "Content-Type": "application/json",
            }

            # Get the project ID first
            project_query = {
                "query": f"""
                query {{
                  user(login: "{self.github_username}") {{
                    projectV2(number: {self.project_number}) {{
                      id
                    }}
                  }}
                }}
                """
            }

            project_response = requests.post(
                graphql_url, headers=graphql_headers, json=project_query
            )
            project_data = project_response.json()
            project_id = (
                project_data.get("data", {})
                .get("user", {})
                .get("projectV2", {})
                .get("id")
            )

            if not project_id:
                return {
                    "success": False,
                    "message": "Could not find GitHub project ID",
                }

            # Update title and body if provided
            if subject or description:
                update_item_query = {
                    "query": f"""
                    mutation {{
                      updateProjectV2DraftIssue(input: {{
                        draftIssueId: "{event_id}"
                        {f'title: "{subject}"' if subject else ''}
                        {f'body: "{description}"' if description else ''}
                      }}) {{
                        draftIssue {{
                          id
                        }}
                      }}
                    }}
                    """
                }

                update_response = requests.post(
                    graphql_url, headers=graphql_headers, json=update_item_query
                )
                if update_response.status_code != 200:
                    logging.error(
                        f"Error updating GitHub draft issue: {update_response.status_code} - {update_response.text}"
                    )
                    return {
                        "success": False,
                        "message": f"Failed to update GitHub project item: {update_response.status_code}: {update_response.text}",
                    }

            # Update due date if provided
            if start_time:
                # Get the date field ID
                fields_query = {
                    "query": f"""
                    query {{
                      node(id: "{project_id}") {{
                        ... on ProjectV2 {{
                          fields(first: 20) {{
                            nodes {{
                              ... on ProjectV2Field {{
                                id
                                name
                              }}
                            }}
                          }}
                        }}
                      }}
                    }}
                    """
                }

                fields_response = requests.post(
                    graphql_url, headers=graphql_headers, json=fields_query
                )
                fields_data = fields_response.json()

                date_field_id = None
                fields = (
                    fields_data.get("data", {})
                    .get("node", {})
                    .get("fields", {})
                    .get("nodes", [])
                )

                for field in fields:
                    if field.get("name", "").lower() in ["due date", "due", "date"]:
                        date_field_id = field.get("id")
                        break

                if date_field_id:
                    due_date = start_time.date().isoformat()
                    update_field_query = {
                        "query": f"""
                        mutation {{
                          updateProjectV2ItemFieldValue(input: {{
                            projectId: "{project_id}"
                            itemId: "{event_id}"
                            fieldId: "{date_field_id}"
                            value: {{
                              date: "{due_date}"
                            }}
                          }}) {{
                            projectV2Item {{
                              id
                            }}
                          }}
                        }}
                        """
                    }

                    update_response = requests.post(
                        graphql_url, headers=graphql_headers, json=update_field_query
                    )
                    if update_response.status_code != 200:
                        logging.error(
                            f"Error updating GitHub item due date: {update_response.status_code} - {update_response.text}"
                        )
                        return {
                            "success": False,
                            "message": f"Failed to update due date on GitHub project item: {update_response.status_code}: {update_response.text}",
                        }

            return {
                "success": True,
                "message": "GitHub project item updated successfully.",
                "event_id": event_id,
            }
        except Exception as e:
            logging.error(f"Error updating GitHub project item: {str(e)}")
            return {
                "success": False,
                "message": f"Failed to update GitHub project item: {str(e)}",
            }

    def delete_event(self, event_id: str) -> str:
        """
        Delete a GitHub Project item (representing a calendar event).
        """
        try:
            self.verify_token()
            graphql_url = f"{self.api_uri}/graphql"
            graphql_headers = {
                "Authorization": f"token {self.access_token}",
                "Content-Type": "application/json",
            }

            # Get the project ID first
            project_query = {
                "query": f"""
                query {{
                  user(login: "{self.github_username}") {{
                    projectV2(number: {self.project_number}) {{
                      id
                    }}
                  }}
                }}
                """
            }

            project_response = requests.post(
                graphql_url, headers=graphql_headers, json=project_query
            )
            project_data = project_response.json()
            project_id = (
                project_data.get("data", {})
                .get("user", {})
                .get("projectV2", {})
                .get("id")
            )

            if not project_id:
                return "Could not find GitHub project ID"

            # Delete the item
            delete_query = {
                "query": f"""
                mutation {{
                  deleteProjectV2Item(input: {{
                    projectId: "{project_id}"
                    itemId: "{event_id}"
                  }}) {{
                    deletedItemId
                  }}
                }}
                """
            }

            delete_response = requests.post(
                graphql_url, headers=graphql_headers, json=delete_query
            )
            if delete_response.status_code != 200:
                logging.error(
                    f"Error deleting GitHub project item: {delete_response.status_code} - {delete_response.text}"
                )
                return f"Failed to delete GitHub project item: {delete_response.status_code}: {delete_response.text}"

            return "GitHub project item deleted successfully."
        except Exception as e:
            logging.error(f"Error deleting GitHub project item: {str(e)}")
            return f"Failed to delete GitHub project item: {str(e)}"

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
        Find available time slots based on GitHub Project items with due dates.
        Note: This is a simulated availability since GitHub Projects has dates but not times.
        """
        try:
            self.verify_token()

            # Get all items with due dates
            events = self.get_events(
                start_date=start_date, end_date=start_date + timedelta(days=num_days)
            )

            # Set up workday time boundaries
            day_start = datetime.strptime(work_day_start, "%H:%M").time()
            day_end = datetime.strptime(work_day_end, "%H:%M").time()

            # Find available slots
            available_slots = []
            current_date = start_date.date()
            end_search_date = (start_date + timedelta(days=num_days)).date()

            while current_date < end_search_date:
                # Create datetime objects for current day start and end
                current_day_start = datetime.combine(current_date, day_start)
                current_day_end = datetime.combine(current_date, day_end)

                # Filter events for the current day
                day_events = []
                for event in events:
                    try:
                        event_date = datetime.fromisoformat(
                            event["start_time"].replace("Z", "+00:00")
                        ).date()

                        if event_date == current_date:
                            # GitHub Projects have dates but not times, so assign standard time ranges
                            # Simulate time by assigning 1-hour blocks during the workday
                            event_start = datetime.combine(current_date, day_start)
                            event_end = event_start + timedelta(hours=1)
                            day_events.append((event_start, event_end))
                    except (ValueError, TypeError, AttributeError):
                        continue

                # Sort events by start time
                day_events.sort(key=lambda x: x[0])

                # If no events, the whole day is available
                if not day_events:
                    # Add slots every `duration_minutes` from day_start to day_end
                    slot_start = current_day_start
                    while slot_start < current_day_end:
                        slot_end = slot_start + timedelta(minutes=duration_minutes)
                        if slot_end <= current_day_end:
                            available_slots.append(
                                {
                                    "start_time": slot_start.isoformat(),
                                    "end_time": slot_end.isoformat(),
                                }
                            )
                            slot_start += timedelta(
                                minutes=duration_minutes + buffer_minutes
                            )
                        else:
                            break
                else:
                    # Start at beginning of workday
                    slot_start = current_day_start

                    # Find free slots between events
                    for event_start, event_end in day_events:
                        # Create a potential slot before the current event if there's enough time
                        if slot_start < event_start:
                            # Add slots every `duration_minutes` from slot_start to event_start
                            while slot_start < event_start:
                                slot_end = slot_start + timedelta(
                                    minutes=duration_minutes
                                )
                                if slot_end <= event_start:
                                    available_slots.append(
                                        {
                                            "start_time": slot_start.isoformat(),
                                            "end_time": slot_end.isoformat(),
                                        }
                                    )
                                    slot_start += timedelta(
                                        minutes=duration_minutes + buffer_minutes
                                    )
                                else:
                                    break

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
                f"Error finding available timeslots in GitHub Projects: {str(e)}"
            )
            return []

    def check_time_availability(
        self, start_time: datetime, end_time: datetime
    ) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Check if a specific time slot conflicts with GitHub Project items with due dates.
        Note: This is a simulated check since GitHub Projects has dates but not times.
        """
        try:
            self.verify_token()

            # Get all events for the specific date
            events = self.get_events(
                start_date=start_time.replace(
                    hour=0, minute=0, second=0, microsecond=0
                ),
                end_date=start_time.replace(
                    hour=23, minute=59, second=59, microsecond=999
                ),
            )

            # For GitHub Projects, we'll consider the day available if there are fewer than 4 items due that day
            # This is a simplification since GitHub Projects doesn't have time slots
            if len(events) >= 4:
                # Return the first event as a "conflict"
                return False, {
                    "id": events[0].get("id", ""),
                    "subject": events[0].get("subject", "Busy Day"),
                    "start_time": events[0].get("start_time", ""),
                    "end_time": events[0].get("end_time", ""),
                }

            # Otherwise, time is available
            return True, None
        except Exception as e:
            logging.error(
                f"Error checking time availability in GitHub Projects: {str(e)}"
            )
            raise

    def get_platform_name(self) -> str:
        """
        Get the name of the calendar platform this provider interacts with.
        """
        return "GitHub Projects"
