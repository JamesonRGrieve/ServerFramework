"""
Domain injections for the Social extension.
"""

domain_injections = {
    "user": {
        "fields": {
            "social_accounts": {
                "type": "relationship",
                "related_entity": "social_accounts",
                "foreign_key": "user_id",
                "getter": "get_social_accounts",
            }
        },
        "methods": {
            "get_social_accounts": {
                "implementation": """
                async def get_social_accounts(self, user_id):
                    from zephyrex.extensions.BLL_Social import SocialAccountManager
                    account_manager = SocialAccountManager(self.db_session)
                    return await account_manager.get_accounts(user_id)
                """
            },
            "add_social_account": {
                "implementation": """
                async def add_social_account(self, user_id, platform, username, access_token, refresh_token=None, token_expires_at=None):
                    from zephyrex.extensions.BLL_Social import SocialAccountManager
                    account_manager = SocialAccountManager(self.db_session)
                    return await account_manager.create_account(
                        user_id=user_id,
                        platform=platform,
                        username=username,
                        access_token=access_token,
                        refresh_token=refresh_token,
                        token_expires_at=token_expires_at
                    )
                """
            },
        },
    },
    "agent": {
        "methods": {
            "post_to_social": {
                "implementation": """
                async def post_to_social(self, agent_id, platform, content, media_urls=None):
                    from zephyrex.extensions.BLL_Social import SocialAccountManager, SocialPostManager
                    
                    # Get the agent's configuration
                    agent = await self.get_agent(agent_id)
                    if not agent:
                        return {"success": False, "error": "Agent not found"}
                    
                    # Check if the agent has social accounts configured
                    account_manager = SocialAccountManager(self.db_session)
                    accounts = await account_manager.get_accounts(agent["user_id"])
                    
                    # Find account for the specified platform
                    platform_account = next((acc for acc in accounts if acc["platform"].lower() == platform.lower()), None)
                    if not platform_account:
                        return {"success": False, "error": f"No {platform} account configured for this agent"}
                    
                    # Create the post
                    post_manager = SocialPostManager(self.db_session)
                    formatted_media = [{"type": "image", "url": url} for url in media_urls] if media_urls else None
                    post = await post_manager.create_post(
                        account_id=platform_account["id"],
                        content=content,
                        media_urls=formatted_media
                    )
                    
                    # Here we would typically call the appropriate provider to actually post the content
                    # For now we'll just return the created post
                    return {"success": True, "post": post}
                """
            },
            "schedule_social_post": {
                "implementation": """
                async def schedule_social_post(self, agent_id, platform, content, scheduled_time, media_urls=None):
                    from datetime import datetime
                    from zephyrex.extensions.BLL_Social import SocialAccountManager, SocialPostManager
                    
                    # Get the agent's configuration
                    agent = await self.get_agent(agent_id)
                    if not agent:
                        return {"success": False, "error": "Agent not found"}
                    
                    # Check if the agent has social accounts configured
                    account_manager = SocialAccountManager(self.db_session)
                    accounts = await account_manager.get_accounts(agent["user_id"])
                    
                    # Find account for the specified platform
                    platform_account = next((acc for acc in accounts if acc["platform"].lower() == platform.lower()), None)
                    if not platform_account:
                        return {"success": False, "error": f"No {platform} account configured for this agent"}
                    
                    # Parse scheduled time if it's a string
                    if isinstance(scheduled_time, str):
                        try:
                            scheduled_time = datetime.fromisoformat(scheduled_time)
                        except ValueError:
                            return {"success": False, "error": "Invalid scheduled time format"}
                    
                    # Create the scheduled post
                    post_manager = SocialPostManager(self.db_session)
                    formatted_media = [{"type": "image", "url": url} for url in media_urls] if media_urls else None
                    post = await post_manager.create_post(
                        account_id=platform_account["id"],
                        content=content,
                        media_urls=formatted_media,
                        scheduled_time=scheduled_time
                    )
                    
                    return {"success": True, "post": post}
                """
            },
        }
    },
}
