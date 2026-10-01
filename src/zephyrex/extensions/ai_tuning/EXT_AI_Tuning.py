from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from zephyrex.extensions.AbstractExtensionProvider import (
    AbstractStaticExtension,
    ability,
)
from zephyrex.lib.Dependencies import EXT_Dependency, PIP_Dependency
from zephyrex.lib.Logging import logger


class EXT_AI_Tuning(AbstractStaticExtension):
    """
    AI Tuning extension for AGInfrastructure.

    Provides comprehensive AI model tuning capabilities including fine-tuning,
    hyperparameter optimization, training orchestration, and model evaluation.
    This extension provides static functionality and metadata to organize
    tuning-related components and manage AI model training workflows.

    The extension focuses on:
    - Model fine-tuning and training
    - Hyperparameter optimization and search
    - Training job orchestration and monitoring
    - Model checkpointing and versioning
    - Performance evaluation and metrics tracking
    - Distributed training coordination
    - Model deployment preparation

    Component loading (DB, BLL, EP) is handled automatically by the import system
    based on file naming conventions.
    """

    # Extension metadata
    name = "ai_tuning"
    version = "1.0.0"
    description = (
        "AI Tuning extension for model fine-tuning, hyperparameter optimization, "
        "and training orchestration"
    )

    # Define dependencies
    ext_dependencies = [
        EXT_Dependency(
            name="core",
            friendly_name="Core Extension",
            optional=False,
            reason="Required for base tuning functionality and database operations",
        ),
        EXT_Dependency(
            name="ai_tasks",
            friendly_name="AI Tasks Extension",
            optional=True,
            reason="Integration with task scheduling for training job management",
        ),
    ]

    pip_dependencies = [
        PIP_Dependency(
            name="torch",
            friendly_name="PyTorch Deep Learning Framework",
            optional=False,
            reason="Required for model training and tensor operations",
            semver=">=2.0.0",
        ),
        PIP_Dependency(
            name="transformers",
            friendly_name="Hugging Face Transformers",
            optional=False,
            reason="Required for transformer model fine-tuning",
            semver=">=4.21.0",
        ),
        PIP_Dependency(
            name="accelerate",
            friendly_name="Hugging Face Accelerate",
            optional=True,
            reason="Distributed training and mixed precision support",
            semver=">=0.20.0",
        ),
        PIP_Dependency(
            name="datasets",
            friendly_name="Hugging Face Datasets",
            optional=False,
            reason="Required for data loading and preprocessing",
            semver=">=2.14.0",
        ),
        PIP_Dependency(
            name="optuna",
            friendly_name="Optuna Hyperparameter Optimization",
            optional=True,
            reason="Advanced hyperparameter optimization and search",
            semver=">=3.3.0",
        ),
        PIP_Dependency(
            name="wandb",
            friendly_name="Weights & Biases",
            optional=True,
            reason="Experiment tracking and model monitoring",
            semver=">=0.15.0",
        ),
        PIP_Dependency(
            name="tensorboard",
            friendly_name="TensorBoard",
            optional=True,
            reason="Training visualization and metrics logging",
            semver=">=2.13.0",
        ),
    ]

    sys_dependencies: List[Any] = []

    # Define database tables
    db_tables: List[Any] = []

    # Define what capabilities this extension provides
    capabilities = [
        "model_fine_tuning",
        "hyperparameter_optimization",
        "training_orchestration",
        "model_evaluation",
        "distributed_training",
        "checkpoint_management",
        "metrics_tracking",
    ]

    # Training job types
    TRAINING_TYPES = {
        "fine_tuning": "Fine-tune pre-trained models",
        "full_training": "Train models from scratch",
        "continued_training": "Continue training from checkpoint",
        "hyperparameter_search": "Optimize hyperparameters",
        "evaluation": "Evaluate model performance",
    }

    # Training statuses
    TRAINING_STATUSES = {
        "pending": "Training job is waiting to start",
        "running": "Training is currently in progress",
        "paused": "Training has been paused",
        "completed": "Training completed successfully",
        "failed": "Training failed with errors",
        "cancelled": "Training was cancelled by user",
        "evaluating": "Model is being evaluated",
    }

    # Supported model types
    MODEL_TYPES = {
        "transformers": ["bert", "gpt", "t5", "llama", "mistral", "claude"],
        "computer_vision": ["resnet", "vit", "efficientnet", "yolo", "detectron"],
        "speech": ["wav2vec", "whisper", "speechT5"],
        "multimodal": ["clip", "blip", "flamingo"],
    }

    def __init__(
        self,
        enable_distributed_training: bool = True,
        max_concurrent_jobs: int = 2,
        checkpoint_interval: int = 500,  # steps
        enable_experiment_tracking: bool = True,
        wandb_project: Optional[str] = None,
        tensorboard_log_dir: str = "./logs",
        mixed_precision: bool = True,
        gradient_checkpointing: bool = True,
        max_training_time_hours: int = 24,
        auto_resume_training: bool = True,
        **kwargs,
    ):
        super().__init__(**kwargs)

        # Store configuration
        self.enable_distributed_training = enable_distributed_training
        self.max_concurrent_jobs = max_concurrent_jobs
        self.checkpoint_interval = checkpoint_interval
        self.enable_experiment_tracking = enable_experiment_tracking
        self.wandb_project = wandb_project
        self.tensorboard_log_dir = tensorboard_log_dir
        self.mixed_precision = mixed_precision
        self.gradient_checkpointing = gradient_checkpointing
        self.max_training_time_hours = max_training_time_hours
        self.auto_resume_training = auto_resume_training

        # Provider instance reference
        self.provider = None
        self.training_environment = None
        self.experiment_tracker = None
        self.active_jobs: Dict[str, Any] = {}
        self.training_metrics: Dict[str, Any] = {}

        # Sane defaults so abilities (create_tuning_job, get_model_registry, etc.)
        # work even before on_initialize()/_initialize_training_environment() run.
        self.device: Any = "cpu"
        self.gpu_count: int = 0
        self._initialize_model_registry()

    def on_initialize(self) -> bool:
        """
        Initialize the AI Tuning extension.
        """
        logger.debug("Initializing AI Tuning Extension...")

        try:
            # Create provider instance
            self._create_provider()

            # Initialize training environment
            self._initialize_training_environment()

            # Initialize experiment tracking if enabled
            if self.enable_experiment_tracking:
                self._initialize_experiment_tracking()

            # Register tuning hooks
            self._register_tuning_hooks()

            # Initialize model registry
            self._initialize_model_registry()

            # Setup distributed training if enabled
            if self.enable_distributed_training:
                self._setup_distributed_training()

            # Register capabilities
            for capability in self.capabilities:
                self.register_capability(capability)

            logger.debug("AI Tuning extension initialized successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize AI Tuning extension: {str(e)}")
            return False

    def _create_provider(self):
        """
        Create and configure the tuning provider instance.
        """
        try:
            self.provider = {
                "type": "ai_tuning",
                "training_env": None,
                "experiment_tracker": None,
                "model_registry": None,
                "active_jobs": self.active_jobs,
                "metrics": self.training_metrics,
            }

            logger.debug("AI Tuning provider created successfully")

        except Exception as e:
            logger.error(f"Failed to create AI Tuning provider: {str(e)}")
            self.provider = None

    def _initialize_training_environment(self):
        """Initialize the training environment and check GPU availability."""
        try:
            import torch

            self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            self.gpu_count = (
                torch.cuda.device_count() if torch.cuda.is_available() else 0
            )

            logger.debug(f"Training device: {self.device}")
            logger.debug(f"Available GPUs: {self.gpu_count}")

            # Set up mixed precision if supported
            if self.mixed_precision and torch.cuda.is_available():
                try:
                    from torch.cuda.amp import GradScaler

                    self.grad_scaler = GradScaler()
                    logger.debug("Mixed precision training enabled")
                except ImportError:
                    logger.warning("Mixed precision not available")
                    self.mixed_precision = False

        except ImportError:
            logger.error(
                "PyTorch not available - training functionality will be limited"
            )
            self.device = "cpu"
            self.gpu_count = 0
        except Exception as e:
            logger.error(f"Error initializing training environment: {e}")

    def _initialize_experiment_tracking(self):
        """Initialize experiment tracking systems."""
        try:
            # Initialize Weights & Biases if available
            try:
                import wandb

                self.wandb_available = True
                logger.debug("Weights & Biases available for experiment tracking")
            except ImportError:
                self.wandb_available = False
                logger.debug("Weights & Biases not available")

            # Initialize TensorBoard if available
            try:
                from torch.utils.tensorboard import SummaryWriter

                self.tensorboard_available = True
                logger.debug("TensorBoard available for logging")
            except ImportError:
                self.tensorboard_available = False
                logger.debug("TensorBoard not available")

        except Exception as e:
            logger.error(f"Error initializing experiment tracking: {e}")

    def _register_tuning_hooks(self):
        """
        Register hooks for tuning-related operations.

        The framework's hook registration now requires ``@AbstractStaticExtension.hook(...)``
        to decorate class-level methods directly (methods are discovered via
        ``getmembers(cls, predicate=isfunction)`` on the extension class) — the old
        ``bll_hook`` factory used to decorate a closure defined inside this method no
        longer exists, and even under the old API a nested closure like that was never
        actually discoverable by the hook registry. Tuning-job validation and training-
        start tracking are therefore invoked directly (see ``create_tuning_job`` and
        ``start_training``) rather than through a hook indirection layer.
        """
        try:
            logger.debug("Tuning hooks registered (validation runs inline)")
        except Exception as e:
            logger.error(f"Error registering tuning hooks: {e}")

    def _initialize_model_registry(self):
        """Initialize the model registry for tracking trained models."""
        self.model_registry = {
            "active_models": {},
            "completed_models": {},
            "failed_models": {},
        }

    def _setup_distributed_training(self):
        """Setup distributed training configuration."""
        try:
            import torch.distributed as dist

            if self.gpu_count > 1:
                logger.debug(
                    f"Setting up distributed training with {self.gpu_count} GPUs"
                )
                # In a real implementation, this would setup DDP configuration
            else:
                logger.debug("Single GPU/CPU training mode")

        except ImportError:
            logger.warning("Distributed training not available")
            self.enable_distributed_training = False
        except Exception as e:
            logger.error(f"Error setting up distributed training: {e}")

    def register_capability(self, capability: str):
        """Register a new capability."""
        if capability not in self.capabilities:
            self.capabilities.append(capability)

    def get_registered_capabilities(self) -> Set[str]:
        """Return currently registered capabilities."""
        return set(self.capabilities)

    def get_capabilities(self) -> Set[str]:
        """Return the capabilities this extension provides."""
        return set(self.capabilities)

    @ability("create_tuning_job")
    async def create_tuning_job(
        self,
        job_name: str,
        model_type: str,
        base_model: str,
        training_type: str = "fine_tuning",
        dataset_config: Optional[Dict[str, Any]] = None,
        training_config: Optional[Dict[str, Any]] = None,
        hyperparameters: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a new model tuning job.
        """
        try:
            job_data = {
                "job_name": job_name,
                "model_type": model_type,
                "base_model": base_model,
                "training_type": training_type,
                "dataset_config": dataset_config or {},
                "training_config": training_config or {},
                "hyperparameters": hyperparameters or {},
                "metadata": metadata or {},
                "status": "pending",
                "created_at": datetime.utcnow().isoformat(),
                "device": str(self.device),
                "gpu_count": self.gpu_count,
            }

            # Validate job data
            if not self._validate_tuning_job_data(job_data):
                return {"success": False, "message": "Invalid tuning job configuration"}

            # Generate job ID
            import uuid

            job_id = str(uuid.uuid4())
            job_data["id"] = job_id

            # Store job
            self.active_jobs[job_id] = job_data

            logger.debug(f"Created tuning job: {job_name} ({job_id})")
            return {"success": True, "job": job_data, "job_id": job_id}

        except Exception as e:
            logger.error(f"Error creating tuning job: {e}")
            return {
                "success": False,
                "message": f"Failed to create tuning job: {str(e)}",
            }

    @ability("start_training")
    async def start_training(
        self,
        job_id: str,
        resume_from_checkpoint: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Start training for a tuning job.
        """
        try:
            if job_id not in self.active_jobs:
                return {"success": False, "message": "Job not found"}

            job_data = self.active_jobs[job_id]

            if job_data["status"] != "pending":
                return {
                    "success": False,
                    "message": f"Job is in {job_data['status']} state",
                }

            # Check concurrent job limit
            running_jobs = sum(
                1 for job in self.active_jobs.values() if job["status"] == "running"
            )
            if running_jobs >= self.max_concurrent_jobs:
                return {"success": False, "message": "Maximum concurrent jobs reached"}

            # Update job status
            job_data["status"] = "running"
            job_data["started_at"] = datetime.utcnow().isoformat()
            if resume_from_checkpoint:
                job_data["resumed_from"] = resume_from_checkpoint

            # Start training process (in real implementation, this would launch actual training)
            training_result = await self._start_training_process(job_id, job_data)

            if self.enable_experiment_tracking:
                self._track_training_start(training_result)

            logger.debug(f"Started training for job {job_id}")
            return {"success": True, "job_id": job_id, "training_info": training_result}

        except Exception as e:
            if job_id in self.active_jobs:
                self.active_jobs[job_id]["status"] = "failed"
                self.active_jobs[job_id]["error"] = str(e)

            logger.error(f"Error starting training: {e}")
            return {"success": False, "message": f"Failed to start training: {str(e)}"}

    @ability("pause_training")
    async def pause_training(self, job_id: str) -> Dict[str, Any]:
        """
        Pause a running training job.
        """
        try:
            if job_id not in self.active_jobs:
                return {"success": False, "message": "Job not found"}

            job_data = self.active_jobs[job_id]

            if job_data["status"] != "running":
                return {
                    "success": False,
                    "message": f"Job is not running (status: {job_data['status']})",
                }

            # Update job status
            job_data["status"] = "paused"
            job_data["paused_at"] = datetime.utcnow().isoformat()

            # Create checkpoint before pausing
            checkpoint_info = await self._create_checkpoint(job_id)
            job_data["last_checkpoint"] = checkpoint_info

            logger.debug(f"Paused training for job {job_id}")
            return {"success": True, "job_id": job_id, "checkpoint": checkpoint_info}

        except Exception as e:
            logger.error(f"Error pausing training: {e}")
            return {"success": False, "message": f"Failed to pause training: {str(e)}"}

    @ability("resume_training")
    async def resume_training(self, job_id: str) -> Dict[str, Any]:
        """
        Resume a paused training job.
        """
        try:
            if job_id not in self.active_jobs:
                return {"success": False, "message": "Job not found"}

            job_data = self.active_jobs[job_id]

            if job_data["status"] != "paused":
                return {
                    "success": False,
                    "message": f"Job is not paused (status: {job_data['status']})",
                }

            # Check concurrent job limit
            running_jobs = sum(
                1 for job in self.active_jobs.values() if job["status"] == "running"
            )
            if running_jobs >= self.max_concurrent_jobs:
                return {"success": False, "message": "Maximum concurrent jobs reached"}

            # Update job status
            job_data["status"] = "running"
            job_data["resumed_at"] = datetime.utcnow().isoformat()

            # Resume from last checkpoint
            checkpoint = job_data.get("last_checkpoint")
            training_result = await self._start_training_process(
                job_id, job_data, checkpoint
            )

            logger.debug(f"Resumed training for job {job_id}")
            return {"success": True, "job_id": job_id, "training_info": training_result}

        except Exception as e:
            logger.error(f"Error resuming training: {e}")
            return {"success": False, "message": f"Failed to resume training: {str(e)}"}

    @ability("stop_training")
    async def stop_training(
        self, job_id: str, save_checkpoint: bool = True
    ) -> Dict[str, Any]:
        """
        Stop a training job.
        """
        try:
            if job_id not in self.active_jobs:
                return {"success": False, "message": "Job not found"}

            job_data = self.active_jobs[job_id]

            if job_data["status"] not in ["running", "paused"]:
                return {
                    "success": False,
                    "message": f"Job cannot be stopped (status: {job_data['status']})",
                }

            # Create final checkpoint if requested
            final_checkpoint = None
            if save_checkpoint:
                final_checkpoint = await self._create_checkpoint(job_id)

            # Update job status
            job_data["status"] = "cancelled"
            job_data["stopped_at"] = datetime.utcnow().isoformat()
            if final_checkpoint:
                job_data["final_checkpoint"] = final_checkpoint

            logger.debug(f"Stopped training for job {job_id}")
            return {
                "success": True,
                "job_id": job_id,
                "final_checkpoint": final_checkpoint,
            }

        except Exception as e:
            logger.error(f"Error stopping training: {e}")
            return {"success": False, "message": f"Failed to stop training: {str(e)}"}

    @ability("get_training_status")
    async def get_training_status(self, job_id: str) -> Dict[str, Any]:
        """
        Get the status of a training job.
        """
        try:
            if job_id not in self.active_jobs:
                return {"success": False, "message": "Job not found"}

            job_data = self.active_jobs[job_id].copy()

            # Add current metrics if available
            if job_id in self.training_metrics:
                job_data["current_metrics"] = self.training_metrics[job_id]

            return {"success": True, "job": job_data}

        except Exception as e:
            logger.error(f"Error getting training status: {e}")
            return {
                "success": False,
                "message": f"Failed to get training status: {str(e)}",
            }

    @ability("optimize_hyperparameters")
    async def optimize_hyperparameters(
        self,
        base_job_config: Dict[str, Any],
        search_space: Dict[str, Any],
        optimization_objective: str = "loss",
        n_trials: int = 10,
        timeout_hours: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Optimize hyperparameters using automated search.
        """
        try:
            # Check if Optuna is available
            try:
                import optuna
            except ImportError:
                return {
                    "success": False,
                    "message": "Optuna not available for hyperparameter optimization",
                }

            # Create optimization study
            study_name = (
                f"hyperparam_study_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
            )

            optimization_config = {
                "study_name": study_name,
                "base_config": base_job_config,
                "search_space": search_space,
                "objective": optimization_objective,
                "n_trials": n_trials,
                "timeout_hours": timeout_hours,
                "status": "running",
                "started_at": datetime.utcnow().isoformat(),
                "trials": [],
            }

            # Generate study ID
            import uuid

            study_id = str(uuid.uuid4())

            # In real implementation, this would run actual hyperparameter optimization
            optimization_result = await self._run_hyperparameter_optimization(
                study_id, optimization_config
            )

            logger.debug(f"Started hyperparameter optimization: {study_name}")
            return {
                "success": True,
                "study_id": study_id,
                "optimization_config": optimization_config,
                "result": optimization_result,
            }

        except Exception as e:
            logger.error(f"Error optimizing hyperparameters: {e}")
            return {
                "success": False,
                "message": f"Failed to optimize hyperparameters: {str(e)}",
            }

    @ability("evaluate_model")
    async def evaluate_model(
        self,
        model_path: str,
        evaluation_dataset: str,
        metrics: Optional[List[str]] = None,
        batch_size: int = 32,
    ) -> Dict[str, Any]:
        """
        Evaluate a trained model on a dataset.
        """
        try:
            metrics = metrics or ["accuracy", "loss", "f1_score"]

            evaluation_config = {
                "model_path": model_path,
                "dataset": evaluation_dataset,
                "metrics": metrics,
                "batch_size": batch_size,
                "status": "running",
                "started_at": datetime.utcnow().isoformat(),
            }

            # Generate evaluation ID
            import uuid

            eval_id = str(uuid.uuid4())

            # Run evaluation (in real implementation, this would load and evaluate the model)
            evaluation_result = await self._run_model_evaluation(
                eval_id, evaluation_config
            )

            logger.debug(f"Started model evaluation: {eval_id}")
            return {
                "success": True,
                "evaluation_id": eval_id,
                "config": evaluation_config,
                "result": evaluation_result,
            }

        except Exception as e:
            logger.error(f"Error evaluating model: {e}")
            return {"success": False, "message": f"Failed to evaluate model: {str(e)}"}

    # Helper methods
    def _validate_tuning_job_data(self, job_data: Any) -> bool:
        """Validate tuning job data structure."""
        try:
            if isinstance(job_data, dict):
                required_fields = ["job_name", "model_type", "base_model"]
                return all(field in job_data for field in required_fields)
            else:
                return hasattr(job_data, "job_name") and hasattr(job_data, "model_type")
        except Exception:
            return False

    async def _start_training_process(
        self,
        job_id: str,
        job_data: Dict[str, Any],
        checkpoint: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Start the actual training process."""
        try:
            # Simulate training initialization
            training_info = {
                "process_id": f"train_{job_id[:8]}",
                "started_at": datetime.utcnow().isoformat(),
                "checkpoint_loaded": checkpoint is not None,
                "estimated_duration_hours": 2.0,
            }

            # Initialize metrics tracking
            self.training_metrics[job_id] = {
                "epoch": 0,
                "step": 0,
                "loss": 0.0,
                "learning_rate": 0.001,
                "last_updated": datetime.utcnow().isoformat(),
            }

            return training_info

        except Exception as e:
            raise Exception(f"Failed to start training process: {str(e)}")

    async def _create_checkpoint(self, job_id: str) -> Dict[str, Any]:
        """Create a checkpoint for a training job."""
        try:
            checkpoint_info = {
                "checkpoint_id": f"checkpoint_{job_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
                "job_id": job_id,
                "created_at": datetime.utcnow().isoformat(),
                "step": self.training_metrics.get(job_id, {}).get("step", 0),
                "epoch": self.training_metrics.get(job_id, {}).get("epoch", 0),
                "model_state_size_mb": 150.5,  # Simulated
            }

            return checkpoint_info

        except Exception as e:
            logger.error(f"Error creating checkpoint: {e}")
            return {}

    async def _run_hyperparameter_optimization(
        self, study_id: str, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Run hyperparameter optimization."""
        try:
            # Simulate optimization process
            optimization_result = {
                "best_params": {
                    "learning_rate": 0.0001,
                    "batch_size": 16,
                    "num_epochs": 5,
                },
                "best_value": 0.85,
                "trials_completed": config["n_trials"],
                "optimization_time_minutes": 45.2,
            }

            return optimization_result

        except Exception as e:
            return {"error": str(e)}

    async def _run_model_evaluation(
        self, eval_id: str, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Run model evaluation."""
        try:
            # Simulate evaluation results
            evaluation_result = {
                "metrics": {
                    "accuracy": 0.892,
                    "loss": 0.234,
                    "f1_score": 0.876,
                },
                "evaluation_time_minutes": 12.5,
                "samples_evaluated": 1000,
            }

            return evaluation_result

        except Exception as e:
            return {"error": str(e)}

    def _track_training_start(self, result: Any):
        """Track training start for analytics."""
        # Implementation for tracking training start
        pass

    # Extension lifecycle methods
    def on_start(self) -> bool:
        """
        Lifecycle method called when the extension is started.
        """
        try:
            logger.debug("Starting AI Tuning extension...")

            # Resume any interrupted training jobs if enabled
            if self.auto_resume_training:
                self._resume_training_jobs()

            logger.debug("AI Tuning extension started successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to start AI Tuning extension: {str(e)}")
            return False

    def on_stop(self) -> bool:
        """
        Lifecycle method called when the extension is stopped.

        Clears in-memory job/metrics state — any job still marked "running" is
        considered interrupted rather than gracefully paused, since this is a
        synchronous shutdown hook and cannot await the actual training task.
        """
        try:
            logger.debug("Stopping AI Tuning extension...")

            self.active_jobs.clear()
            self.training_metrics.clear()

            # Close experiment tracking connections
            if self.experiment_tracker:
                try:
                    if hasattr(self.experiment_tracker, "finish"):
                        self.experiment_tracker.finish()
                    logger.debug("Closed experiment tracker")
                except Exception as e:
                    logger.warning(f"Error closing experiment tracker: {str(e)}")

            logger.debug("AI Tuning extension stopped successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to stop AI Tuning extension: {str(e)}")
            return False

    def on_startup(self):
        """Called when the application starts up."""
        logger.debug("AI Tuning extension startup complete")

        # Log training configuration
        logger.debug(f"Max concurrent jobs: {self.max_concurrent_jobs}")
        logger.debug(f"Distributed training: {self.enable_distributed_training}")

    def on_shutdown(self):
        """Called when the application shuts down."""
        logger.debug("AI Tuning extension shutdown complete")

        # Perform final cleanup
        try:
            # Save any pending checkpoints
            for job_id, job_data in self.active_jobs.items():
                if job_data.get("status") == "running":
                    logger.debug(f"Saving final checkpoint for job {job_id}")

        except Exception as e:
            logger.warning(f"Error during shutdown cleanup: {str(e)}")

    def _resume_training_jobs(self):
        """Resume any paused training jobs."""
        try:
            # In real implementation, this would check for paused jobs and resume them
            logger.debug("Checking for training jobs to resume...")
        except Exception as e:
            logger.error(f"Error resuming training jobs: {e}")

    def validate_config(self) -> List[str]:
        """Validate the extension configuration.

        Each check below is an *optional-capability probe*: any failure to import
        or use the library — not just ``ImportError`` — means that capability is
        unavailable in this environment (e.g. a real ``transformers`` install
        raises non-``ImportError`` exceptions while probing an unusable/partial
        ``torch`` install), so exceptions are caught broadly and recorded as an
        issue rather than propagated.
        """
        issues = []

        # Check for required Python packages
        try:
            import torch
        except Exception:
            issues.append(
                "PyTorch not installed - training functionality will not work"
            )

        try:
            import transformers
        except Exception:
            issues.append(
                "Transformers library not installed - model fine-tuning will not work"
            )

        try:
            import datasets
        except Exception:
            issues.append("Datasets library not installed - data loading will not work")

        # Check GPU availability for training
        try:
            import torch

            if not torch.cuda.is_available():
                issues.append("CUDA not available - training will be slow on CPU")
        except Exception:
            pass

        return issues

    def get_required_permissions(self) -> List[str]:
        """Return the list of permissions required by this extension."""
        return [
            "tuning:create",
            "tuning:read",
            "tuning:update",
            "tuning:delete",
            "tuning:start",
            "tuning:stop",
            "models:evaluate",
            "checkpoints:create",
            "checkpoints:load",
        ]

    def has_capability(self, capability: str) -> bool:
        """Check if this extension has a specific capability."""
        return capability in self.capabilities

    def get_training_types(self) -> Dict[str, str]:
        """Get available training types."""
        return self.TRAINING_TYPES.copy()

    def get_training_statuses(self) -> Dict[str, str]:
        """Get available training statuses."""
        return self.TRAINING_STATUSES.copy()

    def get_model_types(self) -> Dict[str, List[str]]:
        """Get supported model types."""
        return self.MODEL_TYPES.copy()

    def get_active_jobs(self) -> Dict[str, Any]:
        """Get currently active training jobs."""
        return self.active_jobs.copy()

    def get_training_metrics(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Get training metrics for a specific job."""
        return self.training_metrics.get(job_id)

    def get_model_registry(self) -> Dict[str, Any]:
        """Get the model registry."""
        return self.model_registry.copy()
