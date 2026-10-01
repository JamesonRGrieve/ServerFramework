import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from zephyrex.extensions.fdm_sla_printing.PRV_3DPrinting import Abstract3DPrinterProvider


class BambuLabsProvider(Abstract3DPrinterProvider):
    """
    Bambu Lab provider implementation for 3D printer control.
    Uses Bambu Lab API to monitor and control Bambu 3D printers.
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
        Initialize the Bambu Lab provider with configuration parameters.
        """
        # Set default API URI if not provided
        if not api_uri:
            api_uri = "https://api.bambulab.com/v1"

        self.device_id = kwargs.get("device_id", "")
        self.device_type = kwargs.get("device_type", "X1C")  # Default to X1 Carbon
        self.mqtt_username = kwargs.get("mqtt_username", "bblp")
        self.mqtt_password = kwargs.get("mqtt_password", "")
        self.use_local = kwargs.get("use_local", True)

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
        Verify Bambu Lab authentication token.
        """
        headers = {"Authorization": f"Bearer {self.access_token}"}
        response = requests.get(f"{self.api_uri}/user/profile", headers=headers)
        if response.status_code != 200:
            logging.error(
                f"Bambu Lab token validation failed: {response.status_code} - {response.text}"
            )

    def get_printer_status(self) -> Dict[str, Any]:
        """
        Get the current status of the Bambu Lab 3D printer.
        """
        try:
            # Try local connection first if enabled
            if self.use_local and self.printer_ip:
                try:
                    local_status = self._get_local_printer_status()
                    if not local_status.get("error", False):
                        return local_status
                except Exception as e:
                    logging.warning(
                        f"Could not connect to printer locally, falling back to cloud API: {str(e)}"
                    )

            # Fall back to cloud API
            self.verify_token()
            headers = {"Authorization": f"Bearer {self.access_token}"}

            # Get device ID if not provided
            if not self.device_id:
                devices_response = requests.get(
                    f"{self.api_uri}/user/devices", headers=headers
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

                self.device_id = devices[0].get("id")
                self.device_type = devices[0].get("dev_product_name", self.device_type)

            # Get printer status
            response = requests.get(
                f"{self.api_uri}/devices/{self.device_id}/status", headers=headers
            )

            if response.status_code != 200:
                return {
                    "error": True,
                    "message": f"Failed to get printer status: {response.status_code}: {response.text}",
                }

            printer_data = response.json()

            # Parse the printer status and extract relevant information
            # Example structure based on Bambu Lab API
            status_data = printer_data.get("status", {})
            gcode_state = status_data.get("gcode_state", "idle")

            # Map Bambu Lab states to common states
            state_map = {
                "IDLE": "idle",
                "RUNNING": "printing",
                "PAUSE": "paused",
                "FINISH": "completed",
                "FAILED": "failed",
            }

            printer_state = state_map.get(gcode_state.upper(), "unknown")

            return {
                "printer_id": self.device_id,
                "printer_name": printer_data.get(
                    "device_name", f"Bambu {self.device_type}"
                ),
                "status": printer_state,
                "connected": status_data.get("online", False),
                "printing": printer_state == "printing",
                "current_job": {
                    "id": status_data.get("subtask_id", ""),
                    "name": status_data.get("subtask_name", "Unknown"),
                    "progress": status_data.get("mc_percent", 0),
                },
                "progress": status_data.get("mc_percent", 0),
                "temperatures": {
                    "bed": {
                        "current": status_data.get("bed_temper", 0),
                        "target": status_data.get("bed_target_temper", 0),
                    },
                    "nozzle": {
                        "current": status_data.get("nozzle_temper", 0),
                        "target": status_data.get("nozzle_target_temper", 0),
                    },
                    "chamber": {
                        "current": status_data.get("chamber_temper", 0),
                        "target": status_data.get("chamber_target_temper", 0),
                    },
                },
                "error": False,
            }
        except Exception as e:
            logging.error(f"Error getting Bambu Lab printer status: {str(e)}")
            return {"error": True, "message": str(e)}

    def _get_local_printer_status(self) -> Dict[str, Any]:
        """
        Get printer status directly from local Bambu Lab printer.
        Uses local API if available.
        """
        try:
            # Bambu Lab printers commonly use port 8883 for MQTT and 80 for HTTP API
            url = f"http://{self.printer_ip}/api/info"

            # Local API requires authentication
            auth = None
            if self.mqtt_username and self.mqtt_password:
                auth = (self.mqtt_username, self.mqtt_password)

            response = requests.get(url, auth=auth, timeout=5)

            if response.status_code != 200:
                return {
                    "error": True,
                    "message": f"Failed to connect to local printer: {response.status_code}",
                }

            printer_data = response.json()

            # Convert local API format to standardized response
            # The exact fields may differ based on printer model and firmware
            return {
                "printer_id": printer_data.get("device_id", self.device_id),
                "printer_name": printer_data.get("name", f"Bambu {self.device_type}"),
                "status": printer_data.get("print", {}).get("status", "unknown"),
                "connected": True,
                "printing": printer_data.get("print", {}).get("status") == "PRINTING",
                "current_job": {
                    "id": printer_data.get("print", {}).get("subtask_id", ""),
                    "name": printer_data.get("print", {}).get(
                        "subtask_name", "Unknown"
                    ),
                    "progress": printer_data.get("print", {}).get("progress", 0),
                },
                "progress": printer_data.get("print", {}).get("progress", 0),
                "temperatures": {
                    "bed": {
                        "current": printer_data.get("temperature", {}).get("bed", 0),
                        "target": printer_data.get("temperature", {}).get(
                            "bed_target", 0
                        ),
                    },
                    "nozzle": {
                        "current": printer_data.get("temperature", {}).get("nozzle", 0),
                        "target": printer_data.get("temperature", {}).get(
                            "nozzle_target", 0
                        ),
                    },
                    "chamber": {
                        "current": printer_data.get("temperature", {}).get(
                            "chamber", 0
                        ),
                        "target": 0,  # Local API might not expose chamber target temperature
                    },
                },
                "error": False,
            }
        except Exception as e:
            logging.error(f"Error connecting to local Bambu Lab printer: {str(e)}")
            return {"error": True, "message": str(e)}

    def start_print_job(
        self, model_id: str, print_settings: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Start a print job with the specified model on Bambu Lab printer.
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
                    "plate_type": "pei",
                    "bed_temp": 60,
                    "nozzle_temp": 220,
                    "ams_mapping": [0],
                }

            # Start print job
            payload = {
                "device_id": self.device_id,
                "file_id": model_id,
                "settings": print_settings,
            }

            response = requests.post(
                f"{self.api_uri}/devices/{self.device_id}/prints",
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
                "job_id": job_data.get("task_id"),
                "printer_id": self.device_id,
                "estimated_time": job_data.get("estimated_time"),
                "error": False,
            }
        except Exception as e:
            logging.error(f"Error starting Bambu Lab print job: {str(e)}")
            return {"error": True, "message": str(e)}

    def pause_print_job(self, job_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Pause the current print job on Bambu Lab printer.
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
                f"{self.api_uri}/devices/{self.device_id}/prints/control",
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
                "printer_id": self.device_id,
                "job_id": job_id,
                "error": False,
            }
        except Exception as e:
            logging.error(f"Error pausing Bambu Lab print job: {str(e)}")
            return {"error": True, "message": str(e)}

    def resume_print_job(self, job_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Resume the current print job on Bambu Lab printer.
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
                f"{self.api_uri}/devices/{self.device_id}/prints/control",
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
                "printer_id": self.device_id,
                "job_id": job_id,
                "error": False,
            }
        except Exception as e:
            logging.error(f"Error resuming Bambu Lab print job: {str(e)}")
            return {"error": True, "message": str(e)}

    def cancel_print_job(self, job_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Cancel the current print job on Bambu Lab printer.
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
            payload = {"action": "stop"}
            response = requests.post(
                f"{self.api_uri}/devices/{self.device_id}/prints/control",
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
                "printer_id": self.device_id,
                "job_id": job_id,
                "error": False,
            }
        except Exception as e:
            logging.error(f"Error cancelling Bambu Lab print job: {str(e)}")
            return {"error": True, "message": str(e)}

    def upload_model(
        self, file_path: str, model_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Upload a 3D model file to Bambu Lab cloud.
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
            if file_size > 100 * 1024 * 1024:  # 100MB limit
                return {
                    "error": True,
                    "message": "File too large. Maximum size is 100MB.",
                }

            # Set model name if not provided
            if not model_name:
                model_name = os.path.basename(file_path)

            # Get file extension to validate
            _, file_extension = os.path.splitext(file_path)
            file_extension = file_extension.lower()
            if file_extension not in [".stl", ".3mf", ".gcode", ".amf", ".obj"]:
                return {
                    "error": True,
                    "message": f"Unsupported file format: {file_extension}. Supported formats are: .stl, .3mf, .gcode, .amf, .obj",
                }

            # Step 1: Initialize upload to get presigned URL
            init_payload = {
                "name": model_name,
                "file_type": file_extension[1:],  # Remove the dot
                "size": file_size,
            }

            init_response = requests.post(
                f"{self.api_uri}/user/files/upload/init",
                headers=headers,
                json=init_payload,
            )

            if init_response.status_code != 200:
                return {
                    "error": True,
                    "message": f"Failed to initialize file upload: {init_response.status_code}: {init_response.text}",
                }

            upload_data = init_response.json()
            upload_url = upload_data.get("upload_url")
            file_id = upload_data.get("file_id")

            if not upload_url or not file_id:
                return {
                    "error": True,
                    "message": "Failed to get upload URL from Bambu Lab API",
                }

            # Step 2: Upload file to presigned URL
            with open(file_path, "rb") as file:
                upload_response = requests.put(
                    upload_url,
                    data=file,
                    headers={"Content-Type": "application/octet-stream"},
                )

                if upload_response.status_code not in [200, 201, 204]:
                    return {
                        "error": True,
                        "message": f"Failed to upload file: {upload_response.status_code}: {upload_response.text}",
                    }

            # Step 3: Finalize upload
            finalize_response = requests.post(
                f"{self.api_uri}/user/files/upload/complete",
                headers=headers,
                json={"file_id": file_id},
            )

            if finalize_response.status_code != 200:
                return {
                    "error": True,
                    "message": f"Failed to finalize file upload: {finalize_response.status_code}: {finalize_response.text}",
                }

            # Wait for processing to complete
            time.sleep(2)

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
            logging.error(f"Error uploading file to Bambu Lab: {str(e)}")
            return {"error": True, "message": str(e)}

    def get_print_jobs(
        self, limit: int = 10, status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get a list of print jobs from Bambu Lab cloud.
        """
        try:
            self.verify_token()
            headers = {"Authorization": f"Bearer {self.access_token}"}

            # Get print jobs
            params: Dict[str, Any] = {"limit": limit}
            if status:
                params["status"] = status

            if not self.device_id:
                # If device_id is not provided, get list of printers and use the first one
                devices_response = requests.get(
                    f"{self.api_uri}/user/devices", headers=headers
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

                self.device_id = devices[0].get("id")

            response = requests.get(
                f"{self.api_uri}/devices/{self.device_id}/prints",
                headers=headers,
                params=params,
            )

            if response.status_code != 200:
                logging.error(
                    f"Error fetching Bambu Lab print jobs: {response.status_code} - {response.text}"
                )
                return []

            jobs_data = response.json().get("prints", [])
            formatted_jobs = []

            for job in jobs_data:
                job_data = {
                    "job_id": job.get("task_id"),
                    "name": job.get("name", "Unnamed Job"),
                    "status": job.get("status", "unknown"),
                    "created_at": job.get("created_at"),
                    "started_at": job.get("started_at"),
                    "completed_at": job.get("completed_at"),
                    "progress": job.get("progress", 0),
                    "estimated_time": job.get("estimated_time"),
                    "printer_id": self.device_id,
                    "file_id": job.get("file_id"),
                }
                formatted_jobs.append(job_data)

            return formatted_jobs
        except Exception as e:
            logging.error(f"Error getting Bambu Lab print jobs: {str(e)}")
            return []

    def get_printer_temperature(self) -> Dict[str, Any]:
        """
        Get the current temperature readings from the Bambu Lab printer.
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
                        "chamber": {
                            "current": 0,
                            "target": 0,
                        },
                    },
                ),
                "error": False,
            }
        except Exception as e:
            logging.error(f"Error getting Bambu Lab printer temperature: {str(e)}")
            return {"error": True, "message": str(e)}

    def set_printer_temperature(
        self, hotend_temp: Optional[int] = None, bed_temp: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Set target temperatures for the Bambu Lab printer.
        """
        try:
            self.verify_token()
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            # Validate temperature values
            if hotend_temp is not None and (hotend_temp < 0 or hotend_temp > 300):
                return {
                    "error": True,
                    "message": "Invalid hotend temperature. Must be between 0 and 300°C.",
                }

            if bed_temp is not None and (bed_temp < 0 or bed_temp > 110):
                return {
                    "error": True,
                    "message": "Invalid bed temperature. Must be between 0 and 110°C.",
                }

            # Prepare payload
            temperatures: Dict[str, Any] = {}
            if hotend_temp is not None:
                temperatures["nozzle"] = hotend_temp
            if bed_temp is not None:
                temperatures["bed"] = bed_temp

            if not temperatures:
                return {
                    "error": True,
                    "message": "No temperature values provided.",
                }

            payload = {
                "temperatures": temperatures,
                "device_id": self.device_id,
            }

            # Send temperature command
            response = requests.post(
                f"{self.api_uri}/devices/{self.device_id}/temperature",
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
                "printer_id": self.device_id,
                "temperature_settings": temperatures,
                "error": False,
            }
        except Exception as e:
            logging.error(f"Error setting Bambu Lab printer temperature: {str(e)}")
            return {"error": True, "message": str(e)}

    def get_platform_name(self) -> str:
        """
        Get the name of the 3D printer platform this provider interacts with.
        """
        return "Bambu Lab"
