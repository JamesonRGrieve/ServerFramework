from unittest.mock import MagicMock, patch

import pytest

from zephyrex.extensions.ai_tuning.EXT_AI_Tuning import EXT_AI_Tuning


class TestEXTAITuning:
    """
    Test suite for EXT_AI_Tuning extension.

    Tests extension initialization, model tuning capabilities, abilities, and training
    functionality. Focuses on testing training job creation, management, hyperparameter
    optimization, and model evaluation functionality rather than component loading.

    Test areas:
    - Extension metadata and configuration
    - Training capabilities and abilities (job creation, training control)
    - PyTorch and ML framework integration
    - Training orchestration and monitoring
    - Hyperparameter optimization and model evaluation
    - Distributed training and checkpoint management
    - Experiment tracking and metrics collection
    """

    expected_abilities = [
        "create_tuning_job",
        "start_training",
        "pause_training",
        "resume_training",
        "stop_training",
        "get_training_status",
        "optimize_hyperparameters",
        "evaluate_model",
    ]

    expected_capabilities = [
        "model_fine_tuning",
        "hyperparameter_optimization",
        "training_orchestration",
        "model_evaluation",
        "distributed_training",
        "checkpoint_management",
        "metrics_tracking",
    ]

    @pytest.fixture
    def extension(self):
        """Create an EXT_AI_Tuning instance for testing."""
        return EXT_AI_Tuning()

    @pytest.fixture
    def mock_torch_available(self):
        """Mock PyTorch library availability"""
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        mock_torch.cuda.device_count.return_value = 2
        mock_torch.device.return_value = "cuda:0"

        # Mock CUDA amp
        mock_amp = MagicMock()
        mock_torch.cuda.amp.GradScaler.return_value = mock_amp

        with patch.dict("sys.modules", {"torch": mock_torch}):
            yield mock_torch

    @pytest.fixture
    def mock_torch_cpu_only(self):
        """Mock PyTorch library with CPU only"""
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        mock_torch.cuda.device_count.return_value = 0
        mock_torch.device.return_value = "cpu"

        with patch.dict("sys.modules", {"torch": mock_torch}):
            yield mock_torch

    @pytest.fixture
    def mock_optuna_available(self):
        """Mock Optuna library availability"""
        mock_optuna = MagicMock()

        with patch.dict("sys.modules", {"optuna": mock_optuna}):
            yield mock_optuna

    @pytest.fixture
    def mock_wandb_available(self):
        """Mock Weights & Biases library availability"""
        mock_wandb = MagicMock()

        with patch.dict("sys.modules", {"wandb": mock_wandb}):
            yield mock_wandb

    @pytest.fixture
    def mock_tensorboard_available(self):
        """Mock TensorBoard library availability"""
        mock_tensorboard = MagicMock()
        mock_writer = MagicMock()
        mock_tensorboard.utils.tensorboard.SummaryWriter.return_value = mock_writer

        with patch.dict(
            "sys.modules",
            {
                "torch.utils.tensorboard": mock_tensorboard.utils.tensorboard,
                "tensorboard": mock_tensorboard,
            },
        ):
            yield mock_tensorboard

    def test_extension_metadata(self, extension):
        """Test extension metadata and basic attributes"""
        assert extension.name == "ai_tuning"
        assert extension.version == "1.0.0"
        assert "AI Tuning extension" in extension.description
        assert hasattr(extension, "capabilities")
        assert hasattr(extension, "ext_dependencies")
        assert hasattr(extension, "pip_dependencies")
        assert hasattr(extension, "db_tables")

    def test_dependencies_structure(self, extension):
        """Test that dependencies are properly structured"""
        # Check extension dependencies
        assert len(extension.ext_dependencies) == 2
        ext_deps = {dep.name: dep for dep in extension.ext_dependencies}

        assert "core" in ext_deps
        assert not ext_deps["core"].optional

        assert "ai_tasks" in ext_deps
        assert ext_deps["ai_tasks"].optional

        # Check pip dependencies
        assert len(extension.pip_dependencies) == 7
        pip_deps = {dep.name: dep for dep in extension.pip_dependencies}

        assert "torch" in pip_deps
        assert ">=2.0.0" in pip_deps["torch"].semver
        assert not pip_deps["torch"].optional

        assert "transformers" in pip_deps
        assert not pip_deps["transformers"].optional

        assert "datasets" in pip_deps
        assert not pip_deps["datasets"].optional

        assert "accelerate" in pip_deps
        assert pip_deps["accelerate"].optional

        assert "optuna" in pip_deps
        assert pip_deps["optuna"].optional

    def test_db_tables_structure(self, extension):
        """AI Tuning currently has no DB-layer models; db_tables stays an empty list."""
        assert isinstance(extension.db_tables, list)
        assert extension.db_tables == []

    def test_training_constants_structure(self, extension):
        """Test that training constants are properly defined"""
        training_types = extension.get_training_types()
        assert isinstance(training_types, dict)
        assert "fine_tuning" in training_types
        assert "full_training" in training_types
        assert "hyperparameter_search" in training_types

        statuses = extension.get_training_statuses()
        assert isinstance(statuses, dict)
        assert "pending" in statuses
        assert "running" in statuses
        assert "completed" in statuses
        assert "failed" in statuses

        model_types = extension.get_model_types()
        assert isinstance(model_types, dict)
        assert "transformers" in model_types
        assert "computer_vision" in model_types

    def test_initialization_with_gpu(self, mock_torch_available):
        """Test extension initialization with GPU available"""
        extension = EXT_AI_Tuning(
            enable_distributed_training=True,
            max_concurrent_jobs=3,
            mixed_precision=True,
            enable_experiment_tracking=True,
        )

        assert extension.enable_distributed_training is True
        assert extension.max_concurrent_jobs == 3
        assert extension.mixed_precision is True
        assert extension.enable_experiment_tracking is True

    def test_initialization_cpu_only(self, mock_torch_cpu_only):
        """Test extension initialization with CPU only"""
        extension = EXT_AI_Tuning(
            enable_distributed_training=False,
            mixed_precision=False,
            auto_resume_training=False,
        )

        assert extension.enable_distributed_training is False
        assert extension.mixed_precision is False
        assert extension.auto_resume_training is False

    def test_initialization_with_custom_settings(self):
        """Test extension initialization with custom settings"""
        extension = EXT_AI_Tuning(
            checkpoint_interval=1000,
            max_training_time_hours=48,
            tensorboard_log_dir="/custom/logs",
        )

        assert extension.checkpoint_interval == 1000
        assert extension.max_training_time_hours == 48
        assert extension.tensorboard_log_dir == "/custom/logs"

    @patch("zephyrex.extensions.ai_tuning.EXT_AI_Tuning.logger")
    def test_on_initialize_success_with_gpu(
        self, mock_logger, mock_torch_available, mock_wandb_available
    ):
        """Test successful extension initialization with GPU"""
        with patch.object(EXT_AI_Tuning, "_register_tuning_hooks") as mock_hooks:
            extension = EXT_AI_Tuning()
            result = extension.on_initialize()

            assert result is True
            mock_hooks.assert_called_once()

    @patch("zephyrex.extensions.ai_tuning.EXT_AI_Tuning.logger")
    def test_on_initialize_failure(self, mock_logger):
        """Test extension initialization failure handling"""
        with patch.object(
            EXT_AI_Tuning,
            "_initialize_training_environment",
            side_effect=Exception("Test error"),
        ):
            extension = EXT_AI_Tuning()
            result = extension.on_initialize()

            assert result is False

    def test_initialize_training_environment_with_gpu(self, mock_torch_available):
        """Test training environment initialization with GPU"""
        extension = EXT_AI_Tuning()
        extension._initialize_training_environment()

        assert hasattr(extension, "device")
        assert hasattr(extension, "gpu_count")

    def test_initialize_training_environment_no_torch(self):
        """Test training environment initialization without PyTorch"""
        with patch.dict("sys.modules", {"torch": None}):
            extension = EXT_AI_Tuning()
            extension._initialize_training_environment()

            assert extension.device == "cpu"
            assert extension.gpu_count == 0

    def test_initialize_experiment_tracking_with_wandb(self, mock_wandb_available):
        """Test experiment tracking initialization with W&B"""
        extension = EXT_AI_Tuning()
        extension._initialize_experiment_tracking()

        assert hasattr(extension, "wandb_available")

    def test_initialize_experiment_tracking_with_tensorboard(
        self, mock_tensorboard_available
    ):
        """Test experiment tracking initialization with TensorBoard"""
        extension = EXT_AI_Tuning()
        extension._initialize_experiment_tracking()

        assert hasattr(extension, "tensorboard_available")

    def test_get_capabilities(self, extension):
        """Test getting extension capabilities"""
        capabilities = extension.get_capabilities()

        assert isinstance(capabilities, set)
        for expected_capability in self.expected_capabilities:
            assert expected_capability in capabilities

    def test_register_capability(self, extension):
        """Test registering new capability"""
        new_capability = "test_tuning_capability"
        extension.register_capability(new_capability)

        assert new_capability in extension.capabilities
        assert new_capability in extension.get_registered_capabilities()

    @pytest.mark.asyncio
    async def test_create_tuning_job_success(self, extension):
        """Test successful tuning job creation"""
        with patch("uuid.uuid4") as mock_uuid:
            mock_uuid.return_value.hex = "test-job-id"
            mock_uuid.return_value.__str__.return_value = "test-job-id"

            result = await extension.create_tuning_job(
                job_name="test_training",
                model_type="transformers",
                base_model="bert-base-uncased",
                training_type="fine_tuning",
                hyperparameters={"learning_rate": 0.001},
            )

            assert result["success"] is True
            assert "job" in result
            assert result["job"]["job_name"] == "test_training"
            assert result["job"]["model_type"] == "transformers"
            assert result["job"]["base_model"] == "bert-base-uncased"

    @pytest.mark.asyncio
    async def test_create_tuning_job_with_config(self, extension):
        """Test tuning job creation with detailed configuration"""
        dataset_config = {
            "train_path": "/data/train.json",
            "val_path": "/data/val.json",
        }
        training_config = {"batch_size": 16, "num_epochs": 3}

        result = await extension.create_tuning_job(
            job_name="advanced_training",
            model_type="computer_vision",
            base_model="resnet50",
            dataset_config=dataset_config,
            training_config=training_config,
        )

        assert result["success"] is True
        assert result["job"]["dataset_config"] == dataset_config
        assert result["job"]["training_config"] == training_config

    @pytest.mark.asyncio
    async def test_create_tuning_job_invalid_data(self, extension):
        """Test tuning job creation with invalid data"""
        # Mock validation to return False
        with patch.object(extension, "_validate_tuning_job_data", return_value=False):
            result = await extension.create_tuning_job(
                job_name="",  # Invalid empty name
                model_type="",
                base_model="",
            )

            assert result["success"] is False
            assert "Invalid tuning job configuration" in result["message"]

    @pytest.mark.asyncio
    async def test_start_training_success(self, extension):
        """Test successful training start"""
        # First create a job
        with patch("uuid.uuid4") as mock_uuid:
            mock_uuid.return_value.__str__.return_value = "test-job-id"

            await extension.create_tuning_job(
                job_name="test_job",
                model_type="transformers",
                base_model="bert-base-uncased",
            )

            result = await extension.start_training(job_id="test-job-id")

            assert result["success"] is True
            assert result["job_id"] == "test-job-id"
            assert "training_info" in result

    @pytest.mark.asyncio
    async def test_start_training_job_not_found(self, extension):
        """Test training start with non-existent job"""
        result = await extension.start_training(job_id="non-existent-job")

        assert result["success"] is False
        assert "Job not found" in result["message"]

    @pytest.mark.asyncio
    async def test_start_training_concurrent_limit(self, extension):
        """Test training start with concurrent job limit"""
        extension.max_concurrent_jobs = 1

        # Create and start first job
        with patch("uuid.uuid4") as mock_uuid:
            mock_uuid.return_value.__str__.return_value = "job1"
            await extension.create_tuning_job(
                job_name="job1", model_type="transformers", base_model="bert"
            )
            await extension.start_training(job_id="job1")

            # Create second job
            mock_uuid.return_value.__str__.return_value = "job2"
            await extension.create_tuning_job(
                job_name="job2", model_type="transformers", base_model="bert"
            )

            # Try to start second job (should fail due to limit)
            result = await extension.start_training(job_id="job2")

            assert result["success"] is False
            assert "Maximum concurrent jobs reached" in result["message"]

    @pytest.mark.asyncio
    async def test_pause_training_success(self, extension):
        """Test successful training pause"""
        # Create and start a job
        with patch("uuid.uuid4") as mock_uuid:
            mock_uuid.return_value.__str__.return_value = "test-job-id"

            await extension.create_tuning_job(
                job_name="test_job", model_type="transformers", base_model="bert"
            )
            await extension.start_training(job_id="test-job-id")

            result = await extension.pause_training(job_id="test-job-id")

            assert result["success"] is True
            assert result["job_id"] == "test-job-id"
            assert "checkpoint" in result

    @pytest.mark.asyncio
    async def test_pause_training_not_running(self, extension):
        """Test pause training when job is not running"""
        with patch("uuid.uuid4") as mock_uuid:
            mock_uuid.return_value.__str__.return_value = "test-job-id"

            await extension.create_tuning_job(
                job_name="test_job", model_type="transformers", base_model="bert"
            )

            result = await extension.pause_training(job_id="test-job-id")

            assert result["success"] is False
            assert "not running" in result["message"]

    @pytest.mark.asyncio
    async def test_resume_training_success(self, extension):
        """Test successful training resume"""
        # Create, start, and pause a job
        with patch("uuid.uuid4") as mock_uuid:
            mock_uuid.return_value.__str__.return_value = "test-job-id"

            await extension.create_tuning_job(
                job_name="test_job", model_type="transformers", base_model="bert"
            )
            await extension.start_training(job_id="test-job-id")
            await extension.pause_training(job_id="test-job-id")

            result = await extension.resume_training(job_id="test-job-id")

            assert result["success"] is True
            assert result["job_id"] == "test-job-id"

    @pytest.mark.asyncio
    async def test_resume_training_not_paused(self, extension):
        """Test resume training when job is not paused"""
        with patch("uuid.uuid4") as mock_uuid:
            mock_uuid.return_value.__str__.return_value = "test-job-id"

            await extension.create_tuning_job(
                job_name="test_job", model_type="transformers", base_model="bert"
            )

            result = await extension.resume_training(job_id="test-job-id")

            assert result["success"] is False
            assert "not paused" in result["message"]

    @pytest.mark.asyncio
    async def test_stop_training_success(self, extension):
        """Test successful training stop"""
        # Create and start a job
        with patch("uuid.uuid4") as mock_uuid:
            mock_uuid.return_value.__str__.return_value = "test-job-id"

            await extension.create_tuning_job(
                job_name="test_job", model_type="transformers", base_model="bert"
            )
            await extension.start_training(job_id="test-job-id")

            result = await extension.stop_training(
                job_id="test-job-id", save_checkpoint=True
            )

            assert result["success"] is True
            assert result["job_id"] == "test-job-id"
            assert "final_checkpoint" in result

    @pytest.mark.asyncio
    async def test_stop_training_no_checkpoint(self, extension):
        """Test training stop without saving checkpoint"""
        # Create and start a job
        with patch("uuid.uuid4") as mock_uuid:
            mock_uuid.return_value.__str__.return_value = "test-job-id"

            await extension.create_tuning_job(
                job_name="test_job", model_type="transformers", base_model="bert"
            )
            await extension.start_training(job_id="test-job-id")

            result = await extension.stop_training(
                job_id="test-job-id", save_checkpoint=False
            )

            assert result["success"] is True
            assert result["final_checkpoint"] is None

    @pytest.mark.asyncio
    async def test_get_training_status_success(self, extension):
        """Test getting training status"""
        # Create a job
        with patch("uuid.uuid4") as mock_uuid:
            mock_uuid.return_value.__str__.return_value = "test-job-id"

            await extension.create_tuning_job(
                job_name="test_job", model_type="transformers", base_model="bert"
            )

            result = await extension.get_training_status(job_id="test-job-id")

            assert result["success"] is True
            assert "job" in result
            assert result["job"]["status"] == "pending"

    @pytest.mark.asyncio
    async def test_get_training_status_with_metrics(self, extension):
        """Test getting training status with metrics"""
        # Create and start a job
        with patch("uuid.uuid4") as mock_uuid:
            mock_uuid.return_value.__str__.return_value = "test-job-id"

            await extension.create_tuning_job(
                job_name="test_job", model_type="transformers", base_model="bert"
            )
            await extension.start_training(job_id="test-job-id")

            result = await extension.get_training_status(job_id="test-job-id")

            assert result["success"] is True
            assert "current_metrics" in result["job"]

    @pytest.mark.asyncio
    async def test_get_training_status_not_found(self, extension):
        """Test getting status of non-existent job"""
        result = await extension.get_training_status(job_id="non-existent")

        assert result["success"] is False
        assert "Job not found" in result["message"]

    @pytest.mark.asyncio
    async def test_optimize_hyperparameters_success(self, mock_optuna_available):
        """Test successful hyperparameter optimization"""
        extension = EXT_AI_Tuning()

        base_config = {"model_type": "transformers", "base_model": "bert"}
        search_space = {"learning_rate": [0.0001, 0.01], "batch_size": [8, 32]}

        result = await extension.optimize_hyperparameters(
            base_job_config=base_config,
            search_space=search_space,
            n_trials=5,
        )

        assert result["success"] is True
        assert "study_id" in result
        assert "optimization_config" in result

    @pytest.mark.asyncio
    async def test_optimize_hyperparameters_no_optuna(self, extension):
        """Test hyperparameter optimization without Optuna"""
        with patch.dict("sys.modules", {"optuna": None}):
            result = await extension.optimize_hyperparameters(
                base_job_config={},
                search_space={},
            )

            assert result["success"] is False
            assert "Optuna not available" in result["message"]

    @pytest.mark.asyncio
    async def test_evaluate_model_success(self, extension):
        """Test successful model evaluation"""
        result = await extension.evaluate_model(
            model_path="/models/test_model",
            evaluation_dataset="test_dataset",
            metrics=["accuracy", "f1_score"],
            batch_size=16,
        )

        assert result["success"] is True
        assert "evaluation_id" in result
        assert "config" in result
        assert "result" in result

    @pytest.mark.asyncio
    async def test_evaluate_model_default_metrics(self, extension):
        """Test model evaluation with default metrics"""
        result = await extension.evaluate_model(
            model_path="/models/test_model",
            evaluation_dataset="test_dataset",
        )

        assert result["success"] is True
        assert result["config"]["metrics"] == ["accuracy", "loss", "f1_score"]

    def test_validate_tuning_job_data_dict_valid(self, extension):
        """Test tuning job data validation with valid dictionary"""
        job_data = {
            "job_name": "test_job",
            "model_type": "transformers",
            "base_model": "bert-base-uncased",
        }
        result = extension._validate_tuning_job_data(job_data)
        assert result is True

    def test_validate_tuning_job_data_dict_invalid(self, extension):
        """Test tuning job data validation with invalid dictionary"""
        job_data = {"job_name": "test_job"}  # Missing required fields
        result = extension._validate_tuning_job_data(job_data)
        assert result is False

    def test_validate_tuning_job_data_object_valid(self, extension):
        """Test tuning job data validation with valid object"""
        job_data = MagicMock()
        job_data.job_name = "test_job"
        job_data.model_type = "transformers"

        result = extension._validate_tuning_job_data(job_data)
        assert result is True

    def test_validate_config_all_libraries_available(self):
        """Test configuration validation when all libraries are available"""
        mock_libs = {
            "torch": MagicMock(),
            "transformers": MagicMock(),
            "datasets": MagicMock(),
        }
        mock_libs["torch"].cuda.is_available.return_value = True

        with patch.dict("sys.modules", mock_libs):
            extension = EXT_AI_Tuning()
            issues = extension.validate_config()

            assert len(issues) == 0

    def test_validate_config_missing_torch(self):
        """Test configuration validation with missing PyTorch"""
        with patch.dict("sys.modules", {"torch": None}):
            extension = EXT_AI_Tuning()
            issues = extension.validate_config()

            assert len(issues) >= 1
            issue_text = " ".join(issues).lower()
            assert "pytorch" in issue_text or "torch" in issue_text

    def test_validate_config_no_cuda(self):
        """Test configuration validation without CUDA"""
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False

        with patch.dict("sys.modules", {"torch": mock_torch}):
            extension = EXT_AI_Tuning()
            issues = extension.validate_config()

            assert any("CUDA not available" in issue for issue in issues)

    def test_get_required_permissions(self, extension):
        """Test getting required permissions"""
        permissions = extension.get_required_permissions()

        assert isinstance(permissions, list)
        assert len(permissions) == 9
        assert "tuning:create" in permissions
        assert "tuning:start" in permissions
        assert "models:evaluate" in permissions
        assert "checkpoints:create" in permissions

    def test_on_start_success(self):
        """Test successful extension start"""
        extension = EXT_AI_Tuning(auto_resume_training=False)
        result = extension.on_start()

        assert result is True

    def test_on_start_with_auto_resume(self):
        """Test extension start with auto-resume enabled"""
        extension = EXT_AI_Tuning(auto_resume_training=True)
        with patch.object(extension, "_resume_training_jobs") as mock_resume:
            result = extension.on_start()

            assert result is True
            mock_resume.assert_called_once()

    def test_on_stop_success(self, extension):
        """Test successful extension stop"""
        # Add some jobs
        extension.active_jobs["job1"] = {"status": "running"}
        extension.active_jobs["job2"] = {"status": "pending"}
        extension.training_metrics["job1"] = {"loss": 0.5}

        result = extension.on_stop()

        assert result is True
        assert len(extension.active_jobs) == 0
        assert len(extension.training_metrics) == 0

    def test_has_capability(self, extension):
        """Test capability checking"""
        assert extension.has_capability("model_fine_tuning") is True
        assert extension.has_capability("hyperparameter_optimization") is True
        assert extension.has_capability("non_existent_capability") is False

    def test_get_active_jobs(self, extension):
        """Test getting active jobs"""
        extension.active_jobs["job1"] = {"name": "test_job1"}
        extension.active_jobs["job2"] = {"name": "test_job2"}

        active = extension.get_active_jobs()

        assert isinstance(active, dict)
        assert len(active) == 2
        assert "job1" in active
        assert "job2" in active

    def test_get_training_metrics(self, extension):
        """Test getting training metrics for a job"""
        extension.training_metrics["job1"] = {"loss": 0.5, "accuracy": 0.85}

        metrics = extension.get_training_metrics("job1")
        assert metrics == {"loss": 0.5, "accuracy": 0.85}

        metrics_none = extension.get_training_metrics("nonexistent")
        assert metrics_none is None

    def test_get_model_registry(self, extension):
        """Test getting model registry"""
        registry = extension.get_model_registry()

        assert isinstance(registry, dict)
        assert "active_models" in registry
        assert "completed_models" in registry
        assert "failed_models" in registry

    def test_abilities_discovery(self):
        """Test that all expected abilities are registered on the extension class.

        Abilities are discovered from ``@ability``-decorated methods at class
        definition time (``AbstractStaticExtension.__init_subclass__`` ->
        ``_discover_static_abilities_with_validation``) and exposed via the
        ``abilities`` classproperty — there is no per-instance ``abilities``
        dict or ``execute_ability()`` dispatcher in the current framework.
        """
        for expected_ability in self.expected_abilities:
            assert expected_ability in EXT_AI_Tuning.abilities

    def test_tuning_hooks_registration(self, extension):
        """Registering tuning hooks should not raise.

        Validation/tracking now run inline from ``create_tuning_job`` and
        ``start_training`` (see ``_register_tuning_hooks`` docstring) rather
        than through a hook-factory indirection layer.
        """
        extension._register_tuning_hooks()

    def test_lifecycle_methods_integration(self, extension):
        """Test integration of lifecycle methods"""
        # Test startup
        extension.on_startup()

        # Test shutdown
        extension.on_shutdown()

        # These methods should not raise exceptions

    @pytest.mark.asyncio
    async def test_create_checkpoint_success(self, extension):
        """Test checkpoint creation"""
        # Setup training metrics
        extension.training_metrics["job1"] = {"step": 100, "epoch": 2}

        checkpoint = await extension._create_checkpoint("job1")

        assert "checkpoint_id" in checkpoint
        assert checkpoint["job_id"] == "job1"
        assert checkpoint["step"] == 100
        assert checkpoint["epoch"] == 2

    @pytest.mark.asyncio
    async def test_start_training_process_success(self, extension):
        """Test internal training process start"""
        job_data = {"job_name": "test", "model_type": "transformers"}

        result = await extension._start_training_process("job1", job_data)

        assert "process_id" in result
        assert "started_at" in result
        assert "job1" in extension.training_metrics

    @pytest.mark.asyncio
    async def test_run_hyperparameter_optimization(self, extension):
        """Test hyperparameter optimization execution"""
        config = {"n_trials": 5, "objective": "loss"}

        result = await extension._run_hyperparameter_optimization("study1", config)

        assert "best_params" in result
        assert "best_value" in result
        assert "trials_completed" in result

    @pytest.mark.asyncio
    async def test_run_model_evaluation(self, extension):
        """Test model evaluation execution"""
        config = {"model_path": "/model", "metrics": ["accuracy"]}

        result = await extension._run_model_evaluation("eval1", config)

        assert "metrics" in result
        assert "evaluation_time_minutes" in result
        assert "samples_evaluated" in result

    def test_distributed_training_setup(self, mock_torch_available):
        """Test distributed training setup"""
        # Mock distributed module
        mock_dist = MagicMock()
        with patch.dict("sys.modules", {"torch.distributed": mock_dist}):
            extension = EXT_AI_Tuning()
            extension.gpu_count = 2
            extension._setup_distributed_training()

            # Should not raise exceptions

    def test_model_registry_initialization(self, extension):
        """Test model registry initialization"""
        extension._initialize_model_registry()

        assert "active_models" in extension.model_registry
        assert "completed_models" in extension.model_registry
        assert "failed_models" in extension.model_registry


if __name__ == "__main__":
    pytest.main([__file__])
