import asyncio
import uuid
from typing import Any, Dict, Type, TypeVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from zephyrex.AbstractTest import AbstractTest
from zephyrex.logic.AbstractService import AbstractService

# Type variable for service class
T = TypeVar("T", bound=AbstractService)

# Set up logging


# Remove SkippedTest class definition
# class SkippedTest(BaseModel):
#     """Model for a skipped test with a reason."""
#
#     name: str
#     reason: str


# Inherit from AbstractTest
class AbstractSVCTest(AbstractTest):
    """
    Abstract base class for testing service components.

    Provides a structured framework for testing services that implement
    the AbstractService interface, with tests for lifecycle management,
    execution, error handling, and other service-specific functionality.

    Features:
    - Service lifecycle testing
    - Update method testing
    - Error handling and retry testing
    - Async operation testing
    - Service configuration testing

    To use this class, extend it and override the class attributes and methods
    as needed for your specific service.
    """

    # Class to be tested
    service_class: Type[T] = None  # type: ignore[assignment, valid-type]

    # Default service initialization parameters
    service_init_params: Dict[str, Any] = {
        "interval_seconds": 1,
        "max_failures": 3,
        "retry_delay_seconds": 1,
    }

    # Mock initialization parameters
    mock_init_params: Dict[str, Any] = {}

    # Tests to skip - Inherited from AbstractTest
    # skip_tests: List[SkippedTest] = []

    # Remove reason_to_skip_test method - Inherited from AbstractTest
    # def reason_to_skip_test(self, test_name: str) -> bool:
    #     """Check if a test should be skipped based on the skip_tests list."""
    #     for skip in self.skip_tests:
    #         if skip.name == test_name:
    #             pytest.skip(skip.reason)
    #             return True
    #     return False

    @pytest.fixture
    def service(self, db: Session, requester_id: str) -> AbstractService:
        """
        Create a service instance for testing.

        Args:
            db: Database session from pytest fixture
            requester_id: ID of the requesting user from pytest fixture

        Returns:
            An instance of the service class being tested
        """
        if not self.service_class:
            pytest.skip("service_class not defined, test cannot run")

        # Merge default parameters with class-specific parameters
        init_params = {**self.service_init_params}

        # Initialize the service
        service = self.service_class(requester_id=requester_id, db=db, **init_params)  # type: ignore[misc]

        yield service

        # Cleanup after tests
        service.cleanup()

    @pytest.fixture
    def mocked_service(self, db: Session, requester_id: str) -> AbstractService:
        """
        Create a service instance with mocked dependencies.

        Args:
            db: Database session from pytest fixture
            requester_id: ID of the requesting user from pytest fixture

        Returns:
            An instance of the service class with mocked dependencies
        """
        if not self.service_class:
            pytest.skip("service_class not defined, test cannot run")

        # Start with default params
        init_params = {**self.service_init_params}

        # Add mock parameters
        init_params.update(self.mock_init_params)

        # Apply mock patches as needed
        with patch.multiple(self.service_class, **self._get_mocks()):
            # Initialize the service
            service = self.service_class(  # type: ignore[misc]
                requester_id=requester_id, db=db, **init_params
            )

            # Mock the update method for testing
            service.update = AsyncMock()

            yield service

            # Cleanup after tests
            service.cleanup()

    def _get_mocks(self) -> Dict[str, MagicMock]:
        """
        Get mocks for service dependencies.
        Override this method to provide specific mocks for your service.

        Returns:
            Dictionary of method names to mock objects
        """
        return {}

    async def test_service_lifecycle(self, mocked_service):
        """Test the service lifecycle methods (start, stop, pause, resume)."""
        # Test start
        assert not mocked_service.running, "Service should not be running initially"
        mocked_service.start()
        assert mocked_service.running, "Service should be running after start"
        assert not mocked_service.paused, "Service should not be paused after start"

        # Test pause
        mocked_service.pause()
        assert mocked_service.running, "Service should still be running when paused"
        assert mocked_service.paused, "Service should be paused after pause"

        # Test resume
        mocked_service.resume()
        assert mocked_service.running, "Service should still be running after resume"
        assert not mocked_service.paused, "Service should not be paused after resume"

        # Test stop
        mocked_service.stop()
        assert not mocked_service.running, "Service should not be running after stop"

    async def test_run_service_loop(self, mocked_service):
        """Test the service loop execution."""
        # Start the service
        mocked_service.start()

        # Run the service loop for a short time
        task = asyncio.create_task(self._run_service_loop_for_time(mocked_service, 2))

        # Wait for the task to complete
        await task

        # Check that update was called
        mocked_service.update.assert_called(), "Service update method should be called"

    async def _run_service_loop_for_time(self, service, seconds):
        """Run the service loop for a specified number of seconds."""
        # Create a task for the service loop
        loop_task = asyncio.create_task(service.run_service_loop())

        # Wait for specified time
        await asyncio.sleep(seconds)

        # Stop the service to exit the loop
        service.stop()

        # Wait for the loop task to complete
        try:
            await asyncio.wait_for(loop_task, timeout=1)
        except asyncio.TimeoutError:
            # If the task doesn't complete in time, cancel it
            loop_task.cancel()
            try:
                await loop_task
            except asyncio.CancelledError:
                pass

    async def test_error_handling(self, mocked_service):
        """Test service error handling and retry logic."""
        # Configure update method to raise an exception
        error_message = f"Test error {uuid.uuid4()}"
        mocked_service.update.side_effect = Exception(error_message)

        # Start the service
        mocked_service.start()

        # Run the service loop for a short time
        task = asyncio.create_task(self._run_service_loop_for_time(mocked_service, 3))

        # Wait for the task to complete
        await task

        # Check that failure count was incremented
        assert mocked_service.failures > 0, "Failure count should be incremented"

        # Check that update was called multiple times (retries)
        assert (
            mocked_service.update.call_count > 1
        ), "Service should retry after failure"

    async def test_max_failures(self, mocked_service):
        """Test that service stops after reaching max failures."""
        # Configure update method to raise an exception
        error_message = f"Test error {uuid.uuid4()}"
        mocked_service.update.side_effect = Exception(error_message)

        # Set a low max_failures value
        mocked_service.max_failures = 2

        # Start the service
        mocked_service.start()

        # Run the service loop until it stops due to max failures
        task = asyncio.create_task(self._run_service_loop_for_time(mocked_service, 5))

        # Wait for the task to complete
        await task

        # Check that service is no longer running
        assert not mocked_service.running, "Service should stop after max failures"
        assert (
            mocked_service.failures > mocked_service.max_failures
        ), "Failures should exceed max failures"

    async def test_pause_resume(self, mocked_service):
        """Test that paused service doesn't call update."""
        # Start the service
        mocked_service.start()

        # Run for a short time to ensure update is called
        task = asyncio.create_task(self._run_service_loop_for_time(mocked_service, 2))
        await task

        # Reset the mock
        mocked_service.update.reset_mock()

        # Pause the service
        mocked_service.pause()

        # Run for a short time while paused
        task = asyncio.create_task(self._run_service_loop_for_time(mocked_service, 2))
        await task

        # Check that update was not called
        mocked_service.update.assert_not_called(), "Update should not be called when paused"

        # Resume the service
        mocked_service.resume()

        # Run for a short time after resuming
        task = asyncio.create_task(self._run_service_loop_for_time(mocked_service, 2))
        await task

        # Check that update was called after resuming
        mocked_service.update.assert_called(), "Update should be called after resuming"

    async def test_cleanup(self, mocked_service):
        """Test service cleanup."""
        # Start the service
        mocked_service.start()
        assert mocked_service.running, "Service should be running after start"

        # Call cleanup
        mocked_service.cleanup()

        # Service should no longer be running
        assert not mocked_service.running, "Service should not be running after cleanup"

    def test_db_property(self, service, db):
        """Test that db property returns an active session."""
        # Get the db from the service
        service_db = service.db

        # Check that it's not None
        assert service_db is not None, "DB session should not be None"

        # Check that session is active
        assert service_db.is_active, "DB session should be active"

        # Check if it's the same as the provided db session
        if hasattr(service, "_close_db_on_exit"):
            if not service._close_db_on_exit:
                assert service_db == db, "DB session should be the same as provided"

    def test_reset_failures(self, service):
        """Test that _reset_failures resets the failure counter."""
        # Set failure count to a non-zero value
        service.failures = 5

        # Reset failures
        service._reset_failures()

        # Check that failures is reset to 0
        assert service.failures == 0, "Failure count should be reset to 0"

    def test_handle_failure(self, service):
        """Test that _handle_failure increments failure count."""
        # Remember initial failure count
        initial_failures = service.failures

        # Handle a test failure
        error = Exception("Test error")
        result = service._handle_failure(error)

        # Check that failure count was incremented
        assert (
            service.failures == initial_failures + 1
        ), "Failure count should be incremented"

        # Check that result indicates retry is allowed
        assert result, "Handle failure should return True for retry"

        # Set failures to max_failures to test max failures handling
        service.failures = service.max_failures

        # Handle another failure, should raise exception
        with pytest.raises(Exception):
            service._handle_failure(error)

    def test_configure_service(self):
        """Test that _configure_service is called during initialization."""
        if not self.service_class:
            pytest.skip("service_class not defined, test cannot run")

        # Mock the _configure_service method
        with patch.object(self.service_class, "_configure_service") as mock_configure:
            # Initialize the service
            service = self.service_class(
                requester_id=str(uuid.uuid4()), **self.service_init_params
            )

            # Check that _configure_service was called
            mock_configure.assert_called_once()

            # Clean up
            service.cleanup()
