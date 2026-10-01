"""
Domain injections for the Health extension.
This file defines how the Health extension integrates with existing domain managers.
"""

# Define domain injections for existing system components
domain_injections = {
    # Inject health data into user profile domain
    "UserProfile": {
        "methods": {
            "get_health_summary": {
                "function": lambda manager, user_id: manager.db_session.execute(
                    """
                    SELECT 
                        AVG(weight_kg) as avg_weight,
                        COUNT(DISTINCT DATE(date)) as days_tracked,
                        SUM(calories) as total_calories
                    FROM 
                        health_weight w
                    LEFT JOIN 
                        health_nutrition n ON w.user_id = n.user_id
                    WHERE 
                        w.user_id = :user_id
                        AND w.date >= DATE('now', '-30 days')
                    """,
                    {"user_id": user_id},
                ).fetchone()
            }
        },
        "properties": {
            "has_health_tracking": lambda manager, user: manager.db_session.query(
                "SELECT EXISTS(SELECT 1 FROM health_providers WHERE user_id = :user_id)",
                {"user_id": user.id},
            ).scalar()
        },
    },
    # Inject health commands into agent domain
    "Agent": {
        "commands": {
            "track_weight": {
                "function": lambda manager, agent, weight_kg, date=None: manager.execute_extension_command(
                    extension_name="Health",
                    command_name="log_weight",
                    command_args={
                        "weight_kg": float(weight_kg),
                        "date": date or "today",
                    },
                )
            },
            "track_meal": {
                "function": lambda manager, agent, meal_name, calories, meal_type="meal": manager.execute_extension_command(
                    extension_name="Health",
                    command_name="log_nutrition",
                    command_args={
                        "food_name": meal_name,
                        "calories": float(calories),
                        "meal_type": meal_type,
                        "serving_size": 1,
                        "serving_unit": "serving",
                    },
                )
            },
            "get_health_summary": {
                "function": lambda manager, agent, period="week": manager.execute_extension_command(
                    extension_name="Health",
                    command_name="get_nutrition_summary",
                    command_args={"period": period},
                )
            },
        }
    },
    # Add health data section to dashboard
    "Dashboard": {
        "sections": {
            "health": {
                "title": "Health Tracking",
                "order": 50,
                "renderer": "extensions/Health/views/HealthDashboard.vue",
                "data_provider": lambda manager, user_id: {
                    "weight_history": manager.execute_extension_method(
                        extension_name="Health",
                        manager_name="WeightManager",
                        method_name="get_weight_history",
                        method_args={"user_id": user_id, "start_date": "30 days ago"},
                    ),
                    "nutrition_summary": manager.execute_extension_method(
                        extension_name="Health",
                        manager_name="NutritionManager",
                        method_name="get_nutrition_summary",
                        method_args={"user_id": user_id, "start_date": "7 days ago"},
                    ),
                    "activity_data": manager.execute_extension_method(
                        extension_name="Health",
                        manager_name="ActivityManager",
                        method_name="get_activities",
                        method_args={"user_id": user_id, "start_date": "7 days ago"},
                    ),
                },
            }
        }
    },
    # Add health data to search index
    "Search": {
        "indexers": {
            "health_data": {
                "table": "health_nutrition",
                "id_field": "id",
                "text_fields": ["food_name", "meal_type", "notes"],
                "numeric_fields": ["calories", "protein_g", "carbs_g", "fat_g"],
                "timestamp_field": "date",
                "user_field": "user_id",
            }
        }
    },
    # Inject health data into analytics
    "Analytics": {
        "metrics": {
            "avg_daily_calories": {
                "query": """
                    SELECT 
                        AVG(daily_calories) as value,
                        :period as period
                    FROM (
                        SELECT 
                            DATE(date) as day,
                            SUM(calories) as daily_calories
                        FROM 
                            health_nutrition
                        WHERE 
                            user_id = :user_id
                            AND date >= DATE('now', '-' || :days || ' days')
                        GROUP BY 
                            DATE(date)
                    )
                """,
                "params": lambda user_id, period="month": {
                    "user_id": user_id,
                    "period": period,
                    "days": 30 if period == "month" else 7,
                },
            },
            "weight_change": {
                "query": """
                    SELECT 
                        (
                            (SELECT weight_kg FROM health_weight 
                             WHERE user_id = :user_id 
                             ORDER BY date DESC LIMIT 1)
                            -
                            (SELECT weight_kg FROM health_weight 
                             WHERE user_id = :user_id 
                             AND date <= DATE('now', '-' || :days || ' days')
                             ORDER BY date DESC LIMIT 1)
                        ) as value,
                        :period as period
                """,
                "params": lambda user_id, period="month": {
                    "user_id": user_id,
                    "period": period,
                    "days": 30 if period == "month" else 7,
                },
            },
        }
    },
}
