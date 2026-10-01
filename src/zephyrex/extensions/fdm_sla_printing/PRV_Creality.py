import logging
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from zephyrex.extensions.fdm_sla_printing.PRV_3DPrinting import Abstract3DPrinterProvider


class CrealityProvider(Abstract3DPrinterProvider):
    """
    Creality Cloud provider implementation for 3D printer control.
    Uses Creality Cloud API to monitor and control Creality 3D printers.
    """

    def __init__(
        self,
        api_key: str = "",
        api_uri: str = "",
        access_token: str = "",
        printer_ip: str = "",
        extension_id: Optional[str] = None,
        **kwargs: Any,
    ):
        """
        Initialize the Creality Cloud provider with configuration parameters.
        """
        # Set default API URI if not provided
        if not api_uri:
            api_uri = "https://api.crealitycloud.com/v1"

        self.printer_serial = kwargs.get("printer_serial", "")
        self.printer_model = kwargs.get("printer_model", "Ender-3")
        self.direct_connection = kwargs.get("direct_connection", True)

        super().__init__(
            api_key=api_key,
            api_uri=api_uri,
            access_token=access_token,
            printer_ip=printer_ip,
            extension_id=extension_id,
            **kwargs,
        )

    def verify_token(self) -> None:
        """
        Verify Creality Cloud authentication token.
        """
        headers = {"Authorization": f"Bearer {self.access_token}"}
        response = requests.get(f"{self.api_uri}/user/profile", headers=headers)
        if response.status_code != 200:
            logging.error(
                f"Creality Cloud token validation failed: {response.status_code} - {response.text}"
            )

    def get_printer_status(self) -> Dict[str, Any]:
        """
        Get the current status of the Creality 3D printer.
        """
        try:
            # Try local connection first if enabled
            if self.direct_connection and self.printer_ip:
                try:
                    local_status = self._get_local_printer_status()
                    if not local_status.get("error", False):
                        return local_status
                except Exception as e:
                    logging.warning(
                        f"Could not connect to printer directly, falling back to cloud API: {str(e)}"
                    )

            # Fall back to cloud API
            self.verify_token()
            headers = {"Authorization": f"Bearer {self.access_token}"}

            # Get printer serial if not provided
            if not self.printer_serial:
                devices_response = requests.get(
                    f"{self.api_uri}/devices", headers=headers
                )
                if devices_response.status_code != 200:
                    return {
                        "error": True,
                        "message": f"Failed to get device list: {devices_response.status_code}: {devices_response.text}",
                    }

                devices = devices_response.json().get("devices", [])
                if not devices:
                    return {
                        "error": True,
                        "message": "No printers found in your account",
                    }

                self.printer_serial = devices[0].get("serial_number", "")
                self.printer_model = devices[0].get("model", self.printer_model)

            # Get printer status
            response = requests.get(
                f"{self.api_uri}/devices/{self.printer_serial}/status", headers=headers
            )

            if response.status_code != 200:
                return {
                    "error": True,
                    "message": f"Failed to get printer status: {response.status_code}: {response.text}",
                }

            printer_data = response.json()

            # Extract status and normalize values
            status_data = printer_data.get("status", {})
            state = status_data.get("state", "").lower()

            # Map Creality states to common states
            state_map = {
                "idle": "idle",
                "printing": "printing",
                "paused": "paused",
                "complete": "completed",
                "error": "failed",
                "offline": "offline",
            }

            printer_state = state_map.get(state, "unknown")

            return {
                "printer_id": self.printer_serial,
                "printer_name": printer_data.get(
                    "name", f"Creality {self.printer_model}"
                ),
                "status": printer_state,
                "connected": status_data.get("connected", False),
                "printing": printer_state == "printing",
                "current_job": {
                    "id": status_data.get("job_id", ""),
                    "name": status_data.get("job_name", "Unknown"),
                    "progress": status_data.get("progress", 0)
                    * 100,  # Convert to percentage
                },
                "progress": status_data.get("progress", 0)
                * 100,  # Convert to percentage
                "temperatures": {
                    "bed": {
                        "current": status_data.get("bed_temp", {}).get("current", 0),
                        "target": status_data.get("bed_temp", {}).get("target", 0),
                    },
                    "nozzle": {
                        "current": status_data.get("hotend_temp", {}).get("current", 0),
                        "target": status_data.get("hotend_temp", {}).get("target", 0),
                    },
                },
                "error": False,
            }
        except Exception as e:
            logging.error(f"Error getting Creality Cloud printer status: {str(e)}")
            return {"error": True, "message": str(e)}

    def _get_local_printer_status(self) -> Dict[str, Any]:
        """
        Get printer status directly from local Creality printer.
        Uses local API if available.
        """
        try:
            # Most Creality printers use ESP3D or similar Web API on port 80
            url = f"http://{self.printer_ip}/api/status"

            response = requests.get(url, timeout=5)

            if response.status_code != 200:
                return {
                    "error": True,
                    "message": f"Failed to connect to local printer: {response.status_code}",
                }

            printer_data = response.json()

            # Parse status and map to common format
            # Creality local API response varies by firmware and model
            state = printer_data.get("status", "").lower()
            is_printing = "printing" in state or "busy" in state
            is_paused = "paused" in state or "pause" in state

            temps = printer_data.get("temperature", {})

            # Extract progress from status if available
            progress: float = 0
            if "progress" in printer_data:
                progress = printer_data["progress"]
            elif "status" in printer_data and isinstance(printer_data["status"], str):
                # Try to extract progress from status text
                progress_match = re.search(r"(\d+)%", printer_data["status"])
                if progress_match:
                    progress = float(progress_match.group(1))

            return {
                "printer_id": self.printer_serial or "local_printer",
                "printer_name": f"Creality {self.printer_model}",
                "status": (
                    "printing" if is_printing else ("paused" if is_paused else "idle")
                ),
                "connected": True,
                "printing": is_printing,
                "current_job": {
                    "id": "local_job",
                    "name": printer_data.get("job_name", "Local Print Job"),
                    "progress": progress,
                },
                "progress": progress,
                "temperatures": {
                    "bed": {
                        "current": (
                            temps.get("bed", {}).get("current", 0)
                            if isinstance(temps.get("bed"), dict)
                            else temps.get("bed", 0)
                        ),
                        "target": (
                            temps.get("bed", {}).get("target", 0)
                            if isinstance(temps.get("bed"), dict)
                            else 0
                        ),
                    },
                    "nozzle": {
                        "current": (
                            temps.get("tool0", {}).get("current", 0)
                            if isinstance(temps.get("tool0"), dict)
                            else temps.get("tool0", 0)
                        ),
                        "target": (
                            temps.get("tool0", {}).get("target", 0)
                            if isinstance(temps.get("tool0"), dict)
                            else 0
                        ),
                    },
                },
                "error": False,
            }
        except Exception as e:
            logging.error(f"Error connecting to local Creality printer: {str(e)}")
            return {"error": True, "message": str(e)}

    def start_print_job(
        self, model_id: str, print_settings: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Start a print job with the specified model on Creality Cloud printer.
        """
        try:
            self.verify_token()
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            # Get printer status to check if it's ready
            printer_status = self.get_printer_status()
            if printer_status.get("error", False):
                return printer_status

            if printer_status.get("printing", False):
                return {
                    "error": True,
                    "message": "Printer is currently printing another job. Please wait or cancel the current job.",
                }

            # Set default print settings if not provided
            if not print_settings:
                print_settings = {
                    "bed_temp": 60,
                    "nozzle_temp": 200,
                }

            # Start print job
            payload = {
                "file_id": model_id,
                "settings": print_settings,
            }

            response = requests.post(
                f"{self.api_uri}/devices/{self.printer_serial}/print",
                headers=headers,
                json=payload,
            )

            if response.status_code not in [200, 201, 202]:
                return {
                    "error": True,
                    "message": f"Failed to start print job: {response.status_code}: {response.text}",
                }

            job_data = response.json()
            return {
                "success": True,
                "message": "Print job started successfully.",
                "job_id": job_data.get("job_id"),
                "printer_id": self.printer_serial,
                "estimated_time": job_data.get("estimated_time"),
                "error": False,
            }
        except Exception as e:
            logging.error(f"Error starting Creality Cloud print job: {str(e)}")
            return {"error": True, "message": str(e)}

    def pause_print_job(self, job_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Pause the current print job on Creality Cloud printer.
        """
        try:
            self.verify_token()
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            # Get printer status to check if it's printing
            printer_status = self.get_printer_status()
            if printer_status.get("error", False):
                return printer_status

            if not printer_status.get("printing", False):
                return {
                    "error": True,
                    "message": "Printer is not currently printing a job.",
                }

            # If job_id is not provided, use the current job
            if not job_id and "current_job" in printer_status:
                job_id = printer_status["current_job"].get("id")

            # Pause print job
            payload = {"action": "pause"}
            response = requests.post(
                f"{self.api_uri}/devices/{self.printer_serial}/control",
                headers=headers,
                json=payload,
            )

            if response.status_code not in [200, 202]:
                return {
                    "error": True,
                    "message": f"Failed to pause print job: {response.status_code}: {response.text}",
                }

            return {
                "success": True,
                "message": "Print job paused successfully.",
                "printer_id": self.printer_serial,
                "job_id": job_id,
                "error": False,
            }
        except Exception as e:
            logging.error(f"Error pausing Creality Cloud print job: {str(e)}")
            return {"error": True, "message": str(e)}

    def resume_print_job(self, job_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Resume the current print job on Creality Cloud printer.
        """
        try:
            self.verify_token()
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            # Get printer status to check if it's paused
            printer_status = self.get_printer_status()
            if printer_status.get("error", False):
                return printer_status

            if printer_status.get("status") != "paused":
                return {
                    "error": True,
                    "message": "Printer is not currently paused.",
                }

            # If job_id is not provided, use the current job
            if not job_id and "current_job" in printer_status:
                job_id = printer_status["current_job"].get("id")

            # Resume print job
            payload = {"action": "resume"}
            response = requests.post(
                f"{self.api_uri}/devices/{self.printer_serial}/control",
                headers=headers,
                json=payload,
            )

            if response.status_code not in [200, 202]:
                return {
                    "error": True,
                    "message": f"Failed to resume print job: {response.status_code}: {response.text}",
                }

            return {
                "success": True,
                "message": "Print job resumed successfully.",
                "printer_id": self.printer_serial,
                "job_id": job_id,
                "error": False,
            }
        except Exception as e:
            logging.error(f"Error resuming Creality Cloud print job: {str(e)}")
            return {"error": True, "message": str(e)}

    def cancel_print_job(self, job_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Cancel the current print job on Creality Cloud printer.
        """
        try:
            self.verify_token()
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            # Get printer status to check if it's printing or paused
            printer_status = self.get_printer_status()
            if printer_status.get("error", False):
                return printer_status

            if (
                not printer_status.get("printing", False)
                and printer_status.get("status") != "paused"
            ):
                return {
                    "error": True,
                    "message": "Printer is not currently printing or paused.",
                }

            # If job_id is not provided, use the current job
            if not job_id and "current_job" in printer_status:
                job_id = printer_status["current_job"].get("id")

            # Cancel print job
            payload = {"action": "cancel"}
            response = requests.post(
                f"{self.api_uri}/devices/{self.printer_serial}/control",
                headers=headers,
                json=payload,
            )

            if response.status_code not in [200, 202]:
                return {
                    "error": True,
                    "message": f"Failed to cancel print job: {response.status_code}: {response.text}",
                }

            return {
                "success": True,
                "message": "Print job cancelled successfully.",
                "printer_id": self.printer_serial,
                "job_id": job_id,
                "error": False,
            }
        except Exception as e:
            logging.error(f"Error cancelling Creality Cloud print job: {str(e)}")
            return {"error": True, "message": str(e)}

    def upload_model(
        self, file_path: str, model_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Upload a 3D model file to Creality Cloud.
        """
        try:
            self.verify_token()
            headers = {"Authorization": f"Bearer {self.access_token}"}

            # Validate file exists
            if not os.path.exists(file_path):
                return {
                    "error": True,
                    "message": f"File not found: {file_path}",
                }

            # Get file details
            file_size = os.path.getsize(file_path)
            if file_size > 200 * 1024 * 1024:  # 200MB limit
                return {
                    "error": True,
                    "message": "File too large. Maximum size is 200MB.",
                }

            # Set model name if not provided
            if not model_name:
                model_name = os.path.basename(file_path)

            # Get file extension to validate
            _, file_extension = os.path.splitext(file_path)
            file_extension = file_extension.lower()
            if file_extension not in [".stl", ".gcode", ".3mf", ".obj"]:
                return {
                    "error": True,
                    "message": f"Unsupported file format: {file_extension}. Supported formats are: .stl, .gcode, .3mf, .obj",
                }

            # Upload the file
            with open(file_path, "rb") as file:
                # Get upload URL
                init_response = requests.post(
                    f"{self.api_uri}/files/upload/init",
                    headers=headers,
                    json={
                        "filename": model_name,
                        "file_type": file_extension[1:],
                        "size": file_size,
                    },
                )

                if init_response.status_code != 200:
                    return {
                        "error": True,
                        "message": f"Failed to initialize file upload: {init_response.status_code}: {init_response.text}",
                    }

                upload_data = init_response.json()
                upload_url = upload_data.get("upload_url")
                file_id = upload_data.get("file_id")

                # Upload file to URL
                upload_response = requests.put(
                    upload_url,
                    data=file,
                    headers={"Content-Type": "application/octet-stream"},
                )

                if upload_response.status_code not in [200, 201, 204]:
                    return {
                        "error": True,
                        "message": f"Failed to upload file: {upload_response.status_code}",
                    }

                # Finalize upload
                finalize_response = requests.post(
                    f"{self.api_uri}/files/upload/complete",
                    headers=headers,
                    json={"file_id": file_id},
                )

                if finalize_response.status_code != 200:
                    return {
                        "error": True,
                        "message": f"Failed to finalize file upload: {finalize_response.status_code}: {finalize_response.text}",
                    }

                # Return success
                return {
                    "success": True,
                    "message": "File uploaded successfully.",
                    "file_id": file_id,
                    "file_name": model_name,
                    "file_size": file_size,
                    "upload_date": datetime.now().isoformat(),
                    "error": False,
                }
        except Exception as e:
            logging.error(f"Error uploading file to Creality Cloud: {str(e)}")
            return {"error": True, "message": str(e)}

    def get_print_jobs(
        self, limit: int = 10, status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get a list of print jobs from Creality Cloud.
        """
        try:
            self.verify_token()
            headers = {"Authorization": f"Bearer {self.access_token}"}

            # Get printer serial if not already set
            if not self.printer_serial:
                devices_response = requests.get(
                    f"{self.api_uri}/devices", headers=headers
                )
                if devices_response.status_code != 200:
                    logging.error(
                        f"Failed to get device list: {devices_response.status_code}: {devices_response.text}"
                    )
                    return []

                devices = devices_response.json().get("devices", [])
                if not devices:
                    logging.error("No printers found in your account")
                    return []

                self.printer_serial = devices[0].get("serial_number", "")

            # Get print jobs
            params: Dict[str, Any] = {"limit": limit}
            if status:
                params["status"] = status

            response = requests.get(
                f"{self.api_uri}/devices/{self.printer_serial}/jobs",
                headers=headers,
                params=params,
            )

            if response.status_code != 200:
                logging.error(
                    f"Error fetching Creality Cloud print jobs: {response.status_code} - {response.text}"
                )
                return []

            jobs_data = response.json().get("jobs", [])
            formatted_jobs = []

            for job in jobs_data:
                job_data = {
                    "job_id": job.get("id"),
                    "name": job.get("name", "Unnamed Job"),
                    "status": job.get("status", "unknown"),
                    "created_at": job.get("created_at"),
                    "started_at": job.get("started_at"),
                    "completed_at": job.get("completed_at"),
                    "progress": job.get("progress", 0) * 100,  # Convert to percentage
                    "estimated_time": job.get("estimated_time"),
                    "printer_id": self.printer_serial,
                    "file_id": job.get("file_id"),
                }
                formatted_jobs.append(job_data)

            return formatted_jobs
        except Exception as e:
            logging.error(f"Error getting Creality Cloud print jobs: {str(e)}")
            return []

    def get_printer_temperature(self) -> Dict[str, Any]:
        """
        Get the current temperature readings from the Creality printer.
        """
        try:
            # Get printer status which includes temperature data
            printer_status = self.get_printer_status()
            if printer_status.get("error", False):
                return printer_status

            # Extract temperature information
            return {
                "success": True,
                "temperatures": printer_status.get(
                    "temperatures",
                    {
                        "bed": {
                            "current": 0,
                            "target": 0,
                        },
                        "nozzle": {
                            "current": 0,
                            "target": 0,
                        },
                    },
                ),
                "error": False,
            }
        except Exception as e:
            logging.error(f"Error getting Creality Cloud printer temperature: {str(e)}")
            return {"error": True, "message": str(e)}

    def set_printer_temperature(
        self, hotend_temp: Optional[int] = None, bed_temp: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Set target temperatures for the Creality printer.
        """
        try:
            self.verify_token()
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            # Validate temperature values
            if hotend_temp is not None and (hotend_temp < 0 or hotend_temp > 280):
                return {
                    "error": True,
                    "message": "Invalid hotend temperature. Must be between 0 and 280°C.",
                }

            if bed_temp is not None and (bed_temp < 0 or bed_temp > 110):
                return {
                    "error": True,
                    "message": "Invalid bed temperature. Must be between 0 and 110°C.",
                }

            # Prepare payload
            temperature: Dict[str, Any] = {}
            if hotend_temp is not None:
                temperature["hotend"] = hotend_temp
            if bed_temp is not None:
                temperature["bed"] = bed_temp

            if not temperature:
                return {
                    "error": True,
                    "message": "No temperature values provided.",
                }

            payload = {"temperature": temperature}

            # Send temperature command
            response = requests.post(
                f"{self.api_uri}/devices/{self.printer_serial}/control/temperature",
                headers=headers,
                json=payload,
            )

            if response.status_code not in [200, 202]:
                return {
                    "error": True,
                    "message": f"Failed to set temperature: {response.status_code}: {response.text}",
                }

            return {
                "success": True,
                "message": "Temperature set successfully.",
                "printer_id": self.printer_serial,
                "temperature_settings": temperature,
                "error": False,
            }
        except Exception as e:
            logging.error(f"Error setting Creality Cloud printer temperature: {str(e)}")
            return {"error": True, "message": str(e)}

    def get_platform_name(self) -> str:
        """
        Get the name of the 3D printer platform this provider interacts with.
        """
        return "Creality Cloud"
