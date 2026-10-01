import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from zephyrex.extensions.fdm_sla_printing.PRV_3DPrinting import Abstract3DPrinterProvider


class PrusaProvider(Abstract3DPrinterProvider):
    """
    PrusaConnect provider implementation for 3D printer control.
    Uses PrusaConnect API to monitor and control Prusa 3D printers.
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
        Initialize the PrusaConnect provider with configuration parameters.
        """
        # Set default API URI if not provided
        if not api_uri:
            api_uri = "https://connect.prusa3d.com/api/v1"

        self.printer_id = kwargs.get("printer_id", "")
        self.fingerprint = kwargs.get("fingerprint", "")

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
        Verify PrusaConnect authentication token.
        """
        headers = {"Authorization": f"Bearer {self.access_token}"}
        response = requests.get(f"{self.api_uri}/user", headers=headers)
        if response.status_code != 200:
            logging.error(
                f"PrusaConnect token validation failed: {response.status_code} - {response.text}"
            )

    def get_printer_status(self) -> Dict[str, Any]:
        """
        Get the current status of the Prusa 3D printer.
        """
        try:
            self.verify_token()
            headers = {"Authorization": f"Bearer {self.access_token}"}

            # If direct printer IP is available, try to connect directly first
            if self.printer_ip:
                try:
                    local_response = requests.get(
                        f"http://{self.printer_ip}/api/status", timeout=5
                    )
                    if local_response.status_code == 200:
                        return local_response.json()
                except Exception as e:
                    logging.warning(
                        f"Could not connect to printer directly, falling back to PrusaConnect: {str(e)}"
                    )

            # Get printer status from PrusaConnect
            if not self.printer_id:
                # If printer_id is not provided, get list of printers and use the first one
                printers_response = requests.get(
                    f"{self.api_uri}/printers", headers=headers
                )
                if printers_response.status_code != 200:
                    return {
                        "error": True,
                        "message": f"Failed to get printer list: {printers_response.status_code}: {printers_response.text}",
                    }

                printers = printers_response.json().get("printers", [])
                if not printers:
                    return {
                        "error": True,
                        "message": "No printers found in your account",
                    }

                self.printer_id = printers[0].get("id")

            # Get printer status
            response = requests.get(
                f"{self.api_uri}/printers/{self.printer_id}", headers=headers
            )

            if response.status_code != 200:
                return {
                    "error": True,
                    "message": f"Failed to get printer status: {response.status_code}: {response.text}",
                }

            printer_data = response.json()
            return {
                "printer_id": printer_data.get("id"),
                "printer_name": printer_data.get("name", "Prusa Printer"),
                "status": printer_data.get("status", "unknown"),
                "connected": printer_data.get("connected", False),
                "printing": printer_data.get("printing", False),
                "current_job": printer_data.get("job", {}),
                "progress": printer_data.get("progress", {}).get("completion", 0),
                "temperatures": {
                    "bed": {
                        "current": printer_data.get("telemetry", {}).get("temp_bed", 0),
                        "target": printer_data.get("telemetry", {}).get(
                            "target_bed", 0
                        ),
                    },
                    "nozzle": {
                        "current": printer_data.get("telemetry", {}).get(
                            "temp_nozzle", 0
                        ),
                        "target": printer_data.get("telemetry", {}).get(
                            "target_nozzle", 0
                        ),
                    },
                },
                "error": False,
            }
        except Exception as e:
            logging.error(f"Error getting PrusaConnect printer status: {str(e)}")
            return {"error": True, "message": str(e)}

    def start_print_job(
        self, model_id: str, print_settings: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Start a print job with the specified model on PrusaConnect.
        """
        try:
            self.verify_token()
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            # If print_settings is not provided, use default settings
            if not print_settings:
                print_settings = {}

            # Get printer status first to ensure it's ready
            printer_status = self.get_printer_status()
            if printer_status.get("error", False):
                return printer_status

            if printer_status.get("printing", False):
                return {
                    "error": True,
                    "message": "Printer is currently printing another job. Please wait or cancel the current job.",
                }

            # Start print job
            payload = {
                "job": model_id,
                "settings": print_settings,
            }

            response = requests.post(
                f"{self.api_uri}/printers/{self.printer_id}/print",
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
                "printer_id": self.printer_id,
                "estimated_time": job_data.get("estimated_time"),
                "error": False,
            }
        except Exception as e:
            logging.error(f"Error starting PrusaConnect print job: {str(e)}")
            return {"error": True, "message": str(e)}

    def pause_print_job(self, job_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Pause the current print job on PrusaConnect.
        """
        try:
            self.verify_token()
            headers = {"Authorization": f"Bearer {self.access_token}"}

            # Get printer status first to ensure it's printing
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
            response = requests.post(
                f"{self.api_uri}/printers/{self.printer_id}/pause", headers=headers
            )

            if response.status_code not in [200, 202]:
                return {
                    "error": True,
                    "message": f"Failed to pause print job: {response.status_code}: {response.text}",
                }

            return {
                "success": True,
                "message": "Print job paused successfully.",
                "printer_id": self.printer_id,
                "job_id": job_id,
                "error": False,
            }
        except Exception as e:
            logging.error(f"Error pausing PrusaConnect print job: {str(e)}")
            return {"error": True, "message": str(e)}

    def resume_print_job(self, job_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Resume the current print job on PrusaConnect.
        """
        try:
            self.verify_token()
            headers = {"Authorization": f"Bearer {self.access_token}"}

            # Get printer status first to ensure it's paused
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
            response = requests.post(
                f"{self.api_uri}/printers/{self.printer_id}/resume", headers=headers
            )

            if response.status_code not in [200, 202]:
                return {
                    "error": True,
                    "message": f"Failed to resume print job: {response.status_code}: {response.text}",
                }

            return {
                "success": True,
                "message": "Print job resumed successfully.",
                "printer_id": self.printer_id,
                "job_id": job_id,
                "error": False,
            }
        except Exception as e:
            logging.error(f"Error resuming PrusaConnect print job: {str(e)}")
            return {"error": True, "message": str(e)}

    def cancel_print_job(self, job_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Cancel the current print job on PrusaConnect.
        """
        try:
            self.verify_token()
            headers = {"Authorization": f"Bearer {self.access_token}"}

            # Get printer status first to ensure it's printing or paused
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
            response = requests.post(
                f"{self.api_uri}/printers/{self.printer_id}/cancel", headers=headers
            )

            if response.status_code not in [200, 202]:
                return {
                    "error": True,
                    "message": f"Failed to cancel print job: {response.status_code}: {response.text}",
                }

            return {
                "success": True,
                "message": "Print job cancelled successfully.",
                "printer_id": self.printer_id,
                "job_id": job_id,
                "error": False,
            }
        except Exception as e:
            logging.error(f"Error cancelling PrusaConnect print job: {str(e)}")
            return {"error": True, "message": str(e)}

    def upload_model(
        self, file_path: str, model_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Upload a 3D model file to PrusaConnect.
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
            if file_size > 50 * 1024 * 1024:  # 50MB limit
                return {
                    "error": True,
                    "message": "File too large. Maximum size is 50MB.",
                }

            # Set model name if not provided
            if not model_name:
                model_name = os.path.basename(file_path)

            # Get file extension to validate
            _, file_extension = os.path.splitext(file_path)
            file_extension = file_extension.lower()
            if file_extension not in [".stl", ".gcode", ".3mf", ".obj", ".amf"]:
                return {
                    "error": True,
                    "message": f"Unsupported file format: {file_extension}. Supported formats are: .stl, .gcode, .3mf, .obj, .amf",
                }

            # Upload the file
            with open(file_path, "rb") as file:
                files = {"file": (model_name, file)}
                upload_response = requests.post(
                    f"{self.api_uri}/files", headers=headers, files=files
                )

                if upload_response.status_code not in [200, 201]:
                    return {
                        "error": True,
                        "message": f"Failed to upload file: {upload_response.status_code}: {upload_response.text}",
                    }

                file_data = upload_response.json()
                return {
                    "success": True,
                    "message": "File uploaded successfully.",
                    "file_id": file_data.get("id"),
                    "file_name": file_data.get("name", model_name),
                    "file_size": file_data.get("size", file_size),
                    "upload_date": file_data.get(
                        "uploaded_at", datetime.now().isoformat()
                    ),
                    "error": False,
                }
        except Exception as e:
            logging.error(f"Error uploading file to PrusaConnect: {str(e)}")
            return {"error": True, "message": str(e)}

    def get_print_jobs(
        self, limit: int = 10, status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get a list of print jobs from PrusaConnect.
        """
        try:
            self.verify_token()
            headers = {"Authorization": f"Bearer {self.access_token}"}

            # Get print jobs
            params: Dict[str, Any] = {"limit": limit}
            if status:
                params["status"] = status

            response = requests.get(
                f"{self.api_uri}/print-jobs", headers=headers, params=params
            )

            if response.status_code != 200:
                logging.error(
                    f"Error fetching PrusaConnect print jobs: {response.status_code} - {response.text}"
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
                    "progress": job.get("progress", {}).get("completion", 0),
                    "estimated_time": job.get("estimated_time"),
                    "printer_id": job.get("printer", {}).get("id"),
                    "printer_name": job.get("printer", {}).get("name"),
                }
                formatted_jobs.append(job_data)

            return formatted_jobs
        except Exception as e:
            logging.error(f"Error getting PrusaConnect print jobs: {str(e)}")
            return []

    def get_printer_temperature(self) -> Dict[str, Any]:
        """
        Get the current temperature readings from the Prusa printer.
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
            logging.error(f"Error getting PrusaConnect printer temperature: {str(e)}")
            return {"error": True, "message": str(e)}

    def set_printer_temperature(
        self, hotend_temp: Optional[int] = None, bed_temp: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Set target temperatures for the Prusa printer.
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

            if bed_temp is not None and (bed_temp < 0 or bed_temp > 120):
                return {
                    "error": True,
                    "message": "Invalid bed temperature. Must be between 0 and 120°C.",
                }

            # Prepare payload
            payload: Dict[str, Any] = {}
            if hotend_temp is not None:
                payload["target_nozzle"] = hotend_temp
            if bed_temp is not None:
                payload["target_bed"] = bed_temp

            if not payload:
                return {
                    "error": True,
                    "message": "No temperature values provided.",
                }

            # Send temperature command
            response = requests.post(
                f"{self.api_uri}/printers/{self.printer_id}/temperature",
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
                "printer_id": self.printer_id,
                "temperature_settings": payload,
                "error": False,
            }
        except Exception as e:
            logging.error(f"Error setting PrusaConnect printer temperature: {str(e)}")
            return {"error": True, "message": str(e)}

    def get_platform_name(self) -> str:
        """
        Get the name of the 3D printer platform this provider interacts with.
        """
        return "PrusaConnect"
