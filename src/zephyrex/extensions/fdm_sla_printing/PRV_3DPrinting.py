from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class Abstract3DPrinterProvider(ABC):
    """
    Abstract base class for all 3D printer providers used by the FDM/SLA
    Printing extension (currently PrusaConnect, Bambu Lab, and Creality
    Cloud).

    Mirrors AbstractAutomotiveProvider / AbstractSourceProvider: concrete
    providers are lightweight, directly-instantiated clients that hold their
    own credentials/config (api key, access token, printer ip) and expose
    synchronous printer-control operations. EXT_FDMSLAPrinting's async
    abilities call into these methods directly (no await) so a provider's
    return values are plain dicts/lists rather than awaitables.
    """

    def __init__(
        self,
        api_key: str = "",
        api_uri: str = "",
        access_token: str = "",
        printer_ip: str = "",
        extension_id: Optional[str] = None,
        conversation_directory: str = "",
        **kwargs: Any,
    ) -> None:
        self.api_key = api_key
        self.api_uri = api_uri
        self.access_token = access_token
        self.printer_ip = printer_ip
        self.extension_id = extension_id
        self.conversation_directory = conversation_directory
        self.settings: Dict[str, Any] = kwargs

        platform = self.get_platform_name()
        self.commands = {
            f"Get {platform} Printer Status": self.get_printer_status,
            f"Start {platform} Print Job": self.start_print_job,
            f"Pause {platform} Print Job": self.pause_print_job,
            f"Resume {platform} Print Job": self.resume_print_job,
            f"Cancel {platform} Print Job": self.cancel_print_job,
            f"Upload {platform} Model": self.upload_model,
            f"Get {platform} Print Jobs": self.get_print_jobs,
            f"Get {platform} Temperature": self.get_printer_temperature,
            f"Set {platform} Temperature": self.set_printer_temperature,
        }

    @abstractmethod
    def get_printer_status(self) -> Dict[str, Any]:
        """
        Get the current status of the 3D printer.
        Returns information such as printer state, current job, and completion percentage.
        """

    @abstractmethod
    def start_print_job(
        self, model_id: str, print_settings: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Start a print job with the specified model.
        Print settings can include temperature, layer height, infill, etc.
        """

    @abstractmethod
    def pause_print_job(self, job_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Pause the current print job.
        If job_id is not provided, pause the active print job.
        """

    @abstractmethod
    def resume_print_job(self, job_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Resume the current print job.
        If job_id is not provided, resume the paused print job.
        """

    @abstractmethod
    def cancel_print_job(self, job_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Cancel the current print job.
        If job_id is not provided, cancel the active print job.
        """

    @abstractmethod
    def upload_model(
        self, file_path: str, model_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Upload a 3D model file to the printer.
        Acceptable formats typically include STL, GCODE, 3MF, etc.
        """

    @abstractmethod
    def get_print_jobs(
        self, limit: int = 10, status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get a list of print jobs with their status.
        Can filter by status (e.g., 'printing', 'completed', 'failed').
        """

    @abstractmethod
    def get_printer_temperature(self) -> Dict[str, Any]:
        """
        Get the current temperature readings from the printer.
        Typically includes hotend and bed temperatures (current and target).
        """

    @abstractmethod
    def set_printer_temperature(
        self, hotend_temp: Optional[int] = None, bed_temp: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Set target temperatures for the printer.
        Can set hotend and/or bed temperature.
        """

    @abstractmethod
    def get_platform_name(self) -> str:
        """Get the name of the 3D printer platform this provider interacts with."""

    @staticmethod
    def services() -> List[str]:
        """Return a list of services provided by this provider."""
        return ["3d_printing", "manufacturing", "rapid_prototyping"]

    def get_extension_info(self) -> Dict[str, Any]:
        """Get information about the 3D printer extension/provider."""
        return {
            "name": "3D Printer",
            "platform": self.get_platform_name(),
            "description": f"3D Printer extension for {self.get_platform_name()}",
        }
