import os
import re

import pytest
from faker import Faker

from AbstractTest import ParentEntity
from zephyrex.database.AbstractDBTest import AbstractDBTest
from zephyrex.extensions.AbstractEXTTest import ExtensionServerMixin
from zephyrex.extensions.ai.BLL_AI import AiRequestModel, AiStatisticsModel
from zephyrex.extensions.ai.EXT_AI import EXT_AI

faker = Faker()


def _get_test_dependency_name():
    """Generate a dependency name from the current test file name."""
    filename = os.path.basename(__file__)
    name = re.sub(r"\.py$", "", filename).lower()
    name = re.sub(r"_test$", "_tests", name)
    if not name.endswith("_tests"):
        name += "_tests"
    return name


class TestAIRequest(AbstractDBTest, ExtensionServerMixin):
    class_under_test = AiRequestModel
    extension_class = EXT_AI
    create_fields = {
        "name": lambda: f"ai_request_{faker.unique.uuid4()}",
        "prompt": faker.text,
        "provider_instance_id": lambda: faker.uuid4(),
        "request_type": lambda: faker.random_element(
            ["text_generation", "embedding", "image_generation"]
        ),
        "model_name": lambda: faker.random_element(
            ["gpt-4", "gpt-3.5-turbo", "dall-e-3"]
        ),
    }
    update_fields = {
        "response": "Updated response text",
        "status": "completed",
        "tokens_used": 150,
    }
    unique_field = "name"


class TestAIStatistics(AbstractDBTest, ExtensionServerMixin):
    class_under_test = AiStatisticsModel
    extension_class = EXT_AI
    create_fields = {
        "date": faker.date_this_year,
        "provider_name": lambda: faker.random_element(
            ["OpenAI", "Anthropic", "Google"]
        ),
        "model_name": lambda: faker.random_element(["gpt-4", "claude-3", "gemini-pro"]),
        "request_count": lambda: faker.random_int(min=1, max=1000),
        "tokens_used": lambda: faker.random_int(min=100, max=50000),
        "total_cost": lambda: faker.pyfloat(
            left_digits=3, right_digits=2, positive=True
        ),
    }
    update_fields = {
        "request_count": 500,
        "tokens_used": 25000,
        "success_rate": 0.95,
    }
