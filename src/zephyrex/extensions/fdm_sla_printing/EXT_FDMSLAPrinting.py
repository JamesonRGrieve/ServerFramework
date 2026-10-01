from typing import Any, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import AbstractStaticExtension, ability
from zephyrex.lib.Dependencies import EXT_Dependency, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_FDMSLAPrinting(AbstractStaticExtension):
    """
    3D Printer extension for AGInfrastructure.
    Provides 3D printer monitoring and control functionality for various platforms
    including PrusaConnect, Bambu Lab, and Creality Cloud.

    Component loading (DB, BLL, EP) is handled automatically by the import system
    based on file naming conventions.
    """

    # Extension metadata
    name = "3d_printing"
    version = "1.0.0"
    description = "3D Printer extension for monitoring and controlling 3D printers"

    # Define dependencies
    ext_dependencies = [
        EXT_Dependency(
            name="core",
            friendly_name="Core Extension",
            optional=False,
            reason="Required for base 3D printing functionality",
        ),
        EXT_Dependency(
            name="oauth",
            optional=True,
            friendly_name="OAuth Extension",
            reason="Optional OAuth integration for authenticated 3D printer platforms",
        ),
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="requests",
            friendly_name="HTTP Requests Library",
            optional=False,
            reason="Required for API communications with 3D printer platforms",
            semver=">=2.28.0",
        ),
        PIP_Dependency(
            name="websockets",
            friendly_name="WebSocket Client Library",
            optional=True,
            reason="Required for real-time printer status monitoring",
            semver=">=10.0",
        ),
        PIP_Dependency(
            name="pydantic",
            friendly_name="Pydantic Data Validation",
            optional=False,
            reason="Required for printer data validation",
            semver=">=2.0.0",
        ),
    ]

    sys_dependencies: List[Any] = []

    # Define database tables (none for this extension)
    db_tables: List[Any] = []

    # Define what capabilities this extension provides
    capabilities = [
        "printer_monitoring",
        "print_control",
        "model_upload",
        "temperature_control",
        "job_management",
        "status_reporting",
    ]

    def __init__(
        self,
        printer_platform: str = "prusa",
        api_key: str = "",
        access_token: str = "",
        printer_ip: str = "",
        **kwargs: Any,
    ):
        """
        Initialize the 3D printer extension.
        """
        super().__init__(**kwargs)

        self.printer_platform = printer_platform.lower()
        self.access_token = access_token
        self.printer_ip = printer_ip
        self.api_key = api_key
        self.provider = None
        self.commands: Dict[str, Any] = {}

        # Give each instance its own copy of the mutable class-level
        # capability list so register_capability() on one instance can never
        # leak into the class default (and therefore into sibling instances).
        self.capabilities = list(type(self).capabilities)

    def on_initialize(self) -> bool:
        """Initialize the 3D printer extension with the appropriate provider."""
        logger.debug("Initializing 3D Printing Extension...")

        try:
            self._create_provider()
            self._register_commands()

            # Register capabilities
            for capability in self.capabilities:
                self.register_capability(capability)

            logger.debug("3D Printing extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize 3D Printing extension: {str(e)}")
            return False

    def _create_provider(self) -> None:
        """Create the appropriate 3D printer provider based on printer_platform."""
        try:
            provider_mapping = {
                "prusa": ("zephyrex.extensions.fdm_sla_printing.PRV_Prusa", "PrusaProvider"),
                "bambu": (
                    "zephyrex.extensions.fdm_sla_printing.PRV_BambuLabs",
                    "BambuLabsProvider",
                ),
                "creality": (
                    "zephyrex.extensions.fdm_sla_printing.PRV_Creality",
                    "CrealityProvider",
                ),
            }

            if self.printer_platform not in provider_mapping:
                logger.error(f"Unsupported printer platform: {self.printer_platform}")
                self.provider = None
                return

            module_name, class_name = provider_mapping[self.printer_platform]

            try:
                module = __import__(module_name, fromlist=[class_name])
                provider_class = getattr(module, class_name)

                self.provider = provider_class(
                    api_key=self.api_key,
                    access_token=self.access_token,
                    printer_ip=self.printer_ip,
                    extension_id=self.name,
                    **getattr(self, "settings", {}),
                )

                logger.debug(
                    f"3D printer provider for {self.printer_platform} created successfully"
                )

            except ImportError as e:
                logger.warning(
                    f"Could not import 3D printer provider for {self.printer_platform}: {e}"
                )
                self.provider = None
            except Exception as e:
                logger.error(f"Error creating 3D printer provider: {str(e)}")
                self.provider = None

        except Exception as e:
            logger.error(f"Error in provider creation: {str(e)}")
            self.provider = None

    def _register_commands(self) -> None:
        """Register commands based on available provider."""
        if self.provider and hasattr(self.provider, "commands"):
            self.commands = self.provider.commands
        else:
            # Provide placeholder commands that warn about missing provider
            platform_name = self.printer_platform.upper()
            self.commands = {
                f"Get {platform_name} Printer Status": self._no_provider_warning,
                f"Start {platform_name} Print Job": self._no_provider_warning,
                f"Pause {platform_name} Print Job": self._no_provider_warning,
                f"Resume {platform_name} Print Job": self._no_provider_warning,
                f"Cancel {platform_name} Print Job": self._no_provider_warning,
                f"Upload {platform_name} Model": self._no_provider_warning,
            }

    async def _no_provider_warning(self, *args: Any, **kwargs: Any) -> str:
        """Warning message when a provider is not available."""
        return f"No 3D printer provider available for {self.printer_platform}. Please check your configuration."

    def register_capability(self, capability: str) -> None:
        """Register a new capability."""
        if capability not in self.capabilities:
            self.capabilities.append(capability)

    def get_registered_capabilities(self) -> Set[str]:
        """Return currently registered capabilities."""
        return set(self.capabilities)

    def get_capabilities(self) -> Set[str]:
        """Return the capabilities this extension provides."""
        return set(self.capabilities)

    @ability("get_printer_status")
    async def get_printer_status(self) -> Dict[str, Any]:
        """Get the current status of the 3D printer."""
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            # Provider methods are synchronous (see PRV_3DPrinting) — no await.
            status = self.provider.get_printer_status()
            return {"success": True, "status": status}

        except Exception as e:
            logger.error(f"Error getting printer status: {e}")
            return {
                "success": False,
                "message": f"Error getting printer status: {str(e)}",
            }

    @ability("start_print_job")
    async def start_print_job(
        self, model_id: str, print_settings: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Start a print job with the specified model."""
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            result = self.provider.start_print_job(model_id, print_settings)
            return {"success": True, "result": result, "model_id": model_id}

        except Exception as e:
            logger.error(f"Error starting print job: {e}")
            return {"success": False, "message": f"Error starting print job: {str(e)}"}

    @ability("pause_print_job")
    async def pause_print_job(self, job_id: Optional[str] = None) -> Dict[str, Any]:
        """Pause the current print job."""
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            result = self.provider.pause_print_job(job_id)
            return {"success": True, "result": result, "job_id": job_id}

        except Exception as e:
            logger.error(f"Error pausing print job: {e}")
            return {"success": False, "message": f"Error pausing print job: {str(e)}"}

    @ability("resume_print_job")
    async def resume_print_job(self, job_id: Optional[str] = None) -> Dict[str, Any]:
        """Resume the current print job."""
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            result = self.provider.resume_print_job(job_id)
            return {"success": True, "result": result, "job_id": job_id}

        except Exception as e:
            logger.error(f"Error resuming print job: {e}")
            return {"success": False, "message": f"Error resuming print job: {str(e)}"}

    @ability("cancel_print_job")
    async def cancel_print_job(self, job_id: Optional[str] = None) -> Dict[str, Any]:
        """Cancel the current print job."""
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            result = self.provider.cancel_print_job(job_id)
            return {"success": True, "result": result, "job_id": job_id}

        except Exception as e:
            logger.error(f"Error cancelling print job: {e}")
            return {
                "success": False,
                "message": f"Error cancelling print job: {str(e)}",
            }

    @ability("upload_model")
    async def upload_model(
        self, file_path: str, model_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """Upload a 3D model file to the printer."""
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            result = self.provider.upload_model(file_path, model_name)
            return {
                "success": True,
                "result": result,
                "file_path": file_path,
                "model_name": model_name,
            }

        except Exception as e:
            logger.error(f"Error uploading model: {e}")
            return {"success": False, "message": f"Error uploading model: {str(e)}"}

    @ability("get_print_jobs")
    async def get_print_jobs(
        self, limit: int = 10, status: Optional[str] = None
    ) -> Dict[str, Any]:
        """Get a list of print jobs with their status."""
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            jobs = self.provider.get_print_jobs(limit, status)
            return {"success": True, "jobs": jobs, "count": len(jobs) if jobs else 0}

        except Exception as e:
            logger.error(f"Error getting print jobs: {e}")
            return {"success": False, "message": f"Error getting print jobs: {str(e)}"}

    @ability("get_printer_temperature")
    async def get_printer_temperature(self) -> Dict[str, Any]:
        """Get the current temperature readings from the printer."""
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            temperature = self.provider.get_printer_temperature()
            return {"success": True, "temperature": temperature}

        except Exception as e:
            logger.error(f"Error getting printer temperature: {e}")
            return {
                "success": False,
                "message": f"Error getting printer temperature: {str(e)}",
            }

    @ability("set_printer_temperature")
    async def set_printer_temperature(
        self, hotend_temp: Optional[int] = None, bed_temp: Optional[int] = None
    ) -> Dict[str, Any]:
        """Set target temperatures for the printer."""
        try:
            if not self.provider:
                return {"success": False, "message": await self._no_provider_warning()}

            result = self.provider.set_printer_temperature(hotend_temp, bed_temp)
            return {
                "success": True,
                "result": result,
                "hotend_temp": hotend_temp,
                "bed_temp": bed_temp,
            }

        except Exception as e:
            logger.error(f"Error setting printer temperature: {e}")
            return {
                "success": False,
                "message": f"Error setting printer temperature: {str(e)}",
            }

    def on_start(self) -> bool:
        """Start the 3D Printing extension."""
        try:
            logger.debug("3D Printing extension started successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to start 3D Printing extension: {e}")
            return False

    def on_stop(self) -> bool:
        """Stop the 3D Printing extension."""
        try:
            if self.provider:
                # Clean up provider resources if needed
                self.provider = None

            logger.debug("3D Printing extension stopped successfully")
            return True
        except Exception as e:
            logger.error(f"Error stopping 3D Printing extension: {e}")
            return False

    def validate_config(self) -> List[str]:
        """Validate the extension configuration."""
        issues = []

        # Check for required Python packages
        try:
            import requests  # noqa: F401
        except ImportError:
            issues.append(
                "Requests library not installed - API communications will not work"
            )

        try:
            import pydantic  # noqa: F401
        except ImportError:
            issues.append(
                "Pydantic library not installed - data validation will not work"
            )

        # Platform-specific validation
        if not self.printer_platform:
            issues.append("Printer platform not specified")
        elif self.printer_platform not in ["prusa", "bambu", "creality"]:
            issues.append(f"Unsupported printer platform: {self.printer_platform}")

        # Check required credentials based on platform
        if self.printer_platform == "prusa":
            if not self.api_key and not self.access_token:
                issues.append("PrusaConnect requires either API key or access token")
        elif self.printer_platform == "bambu":
            if not self.access_token:
                issues.append("Bambu Lab requires access token")
        elif self.printer_platform == "creality":
            if not self.api_key:
                issues.append("Creality Cloud requires API key")

        return issues

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
            "3dprinter:read",
            "3dprinter:control",
            "3dprinter:upload",
            "3dprinter:temperature",
            "3dprinter:jobs",
        ]

    def on_startup(self) -> None:
        """Called during application startup."""
        logger.debug("3D Printing extension startup hook called")

    def on_shutdown(self) -> None:
        """Called during application shutdown."""
        logger.debug("3D Printing extension shutdown hook called")

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities
