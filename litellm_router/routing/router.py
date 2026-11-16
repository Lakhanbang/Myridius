import logging
from typing import List, Dict, Any, Optional

from litellm_router.utils.llm_client import LLMClient, LLMRateLimitError, LLMAPIConnectionError, LLMTimeoutError
from litellm_router.utils.text_parser import TextParser
from litellm_router.utils.time_utils import TimeTracker
from litellm_router.config import AppConfig
from litellm_router.routing.load_balancer import AdaptiveLoadBalancer

logger = logging.getLogger(__name__)

# --- ENTERPRISE POOL CONFIGURATION ---
POOLS = {
    "coding": [AppConfig.OPENAI_MODEL, AppConfig.GEMINI_MODEL, AppConfig.GROQ_MODEL],
    "reasoning": [AppConfig.GEMINI_MODEL, AppConfig.OPENAI_MODEL, AppConfig.GROQ_MODEL],
    "chat": [AppConfig.GROQ_MODEL, AppConfig.GEMINI_MODEL, AppConfig.OPENAI_MODEL],
    "summarization": [AppConfig.GEMINI_MODEL, AppConfig.OPENAI_MODEL, AppConfig.GROQ_MODEL],
    "translation": [AppConfig.OPENAI_MODEL, AppConfig.GEMINI_MODEL, AppConfig.GROQ_MODEL],
    "classification": [AppConfig.OPENAI_MODEL, AppConfig.GROQ_MODEL, AppConfig.GEMINI_MODEL],
    "document_analysis": [AppConfig.GROQ_MODEL, AppConfig.OPENAI_MODEL, AppConfig.GEMINI_MODEL],
    "agentic_workflow": [AppConfig.OPENAI_MODEL, AppConfig.GEMINI_MODEL, AppConfig.GROQ_MODEL],
    "creative_writing": [AppConfig.OPENAI_MODEL, AppConfig.GROQ_MODEL, AppConfig.GEMINI_MODEL],
    "default": [AppConfig.OPENAI_MODEL, AppConfig.GEMINI_MODEL, AppConfig.GROQ_MODEL]
}

# --- QUALITY SCORES (Task-Specific, scaled out of 100) ---
QUALITY_SCORES = {
    AppConfig.OPENAI_MODEL: {
        "coding": 95, "reasoning": 95, "chat": 90, "summarization": 90,
        "translation": 88, "classification": 92, "document_analysis":75,
        "agentic_workflow": 95, "creative_writing": 98, "default": 94
    },
    AppConfig.GEMINI_MODEL: {
        "coding": 88, "reasoning": 87, "chat": 88, "summarization": 93,
        "translation": 92, "classification": 86, "document_analysis": 75,
        "agentic_workflow": 85, "creative_writing": 75, "default": 89
    },
    AppConfig.GROQ_MODEL: {
        "coding": 78, "reasoning": 75, "chat": 80, "summarization": 74,
        "translation": 73, "classification": 76, "document_analysis": 95,
        "agentic_workflow": 70, "creative_writing": 86, "default": 76
    }
}

# --- RELATIVE COST SCORES ---
COST_SCORES = {
    AppConfig.GROQ_MODEL: 95,      
    AppConfig.OPENAI_MODEL: 85,     
    AppConfig.GEMINI_MODEL: 90      
}

class EnterpriseRouter:
    """
    Enterprise Multi-Factor Intelligent Routing Engine.
    Coordinates Task Classification, Scoring, and delegates to Load Balancer.
    """
    def __init__(self):
        self.load_balancer = AdaptiveLoadBalancer(AppConfig.ALL_MODELS)
        self.last_skipped = []
        self.last_task_type = ""
        self.last_pool = []
        
    def classify_task(self, messages: List[Dict[str, str]]) -> str:
        """Task Classification Layer (Hybrid: Keyword -> LLM Fallback)."""
        prompt = ""
        for msg in messages:
            if msg.get("role") == "user":
                prompt += msg.get("content", "").lower() + " "
                
        words = TextParser.extract_words(prompt)
                
        if any(word in words for word in ["code", "function", "api", "python", "javascript", "react", "microservice", "jwt"]):
            return "coding"
        elif any(word in words for word in ["think", "logic", "puzzle", "math", "reason"]):
            return "reasoning"
        elif any(word in words for word in ["summarize", "tldr", "shorten"]):
            return "summarization"
        elif any(word in words for word in ["translate", "spanish", "french", "german"]):
            return "translation"
        elif any(word in words for word in ["write", "story", "poem", "creative", "draft", "answer", "drafter", "professional"]):
            return "creative_writing"
        elif any(word in words for word in ["classify", "categorize", "label"]):
            return "classification"
        elif any(word in words for word in ["document", "analyze", "rfp", "extract", "organizer", "questions"]):
            return "document_analysis"
        
        print("[Router] Keywords not matched, falling back to LLM-based classification (Groq)...")
        try:
            class_prompt = f"Categorize the following text into exactly one of these categories: coding, reasoning, summarization, translation, creative_writing, classification, document_analysis, agentic_workflow, or chat. Note: General factual questions, trivia, or simple conversation MUST be categorized as 'chat'. Return ONLY the category name in lowercase.\n\nText: {prompt}"
            response = LLMClient.completion(
                model=AppConfig.GROQ_MODEL,
                messages=[{"role": "user", "content": class_prompt}],
                max_tokens=10,
                temperature=0.0
            )
            llm_class = response.choices[0].message.content.strip().lower()
            valid_categories = ["coding", "reasoning", "summarization", "translation", "creative_writing", "classification", "document_analysis", "agentic_workflow", "chat"]
            
            for category in valid_categories:
                if category in llm_class:
                    print(f"[Router] Groq LLM classified task as: {category.upper()}")
                    return category
        except Exception as e:
            logger.warning(f"LLM classification failed: {e}. Defaulting to chat.")
            
        return "chat"
        
    def calculate_score(self, model: str, task_type: str) -> float:
        """
        Builds the Composite Routing Score based on multiple enterprise signals.
        Formula: 30% Quality + 20% Latency + 40% Cost + 10% Success
        """
        state = self.load_balancer.get_state(model)
        
        if state.status == "Unhealthy":
            return -1.0
            
        quality = QUALITY_SCORES.get(model, {}).get(task_type, QUALITY_SCORES.get(model, {}).get("default", 80))
        latency_score = max(0, 100 - (state.latency_ms / 100.0)) 
        cost = COST_SCORES.get(model, 80)
        success = state.success_rate * 100
        
        final_score = (
            (0.30 * quality) + (0.20 * latency_score) + (0.40 * cost) + (0.10 * success)
        )
        return final_score

    def completion(self, messages: List[Dict[str, str]], force_models: Optional[List[str]] = None, **kwargs) -> Any:
        """Main entry point for gateway requests."""
        self.last_skipped = []
        if force_models:
            self.last_task_type = "manager_workflow"
            print(f"[Router] Explicit Priority Routing Requested: {force_models}")
            target_models = []
            scored_models = []
            for m in force_models:
                state = self.load_balancer.get_state(m)
                if state.status != "Unhealthy":
                    target_models.append(m)
                    scored_models.append((m, 100.0))
            if not target_models:
                target_models = list(force_models)
                scored_models = [(m, 0.0) for m in target_models]
        else:
            self.last_task_type = self.classify_task(messages)
            print(f"[Router] Request Classified As: {self.last_task_type.upper()}")
            
            pool = POOLS.get(self.last_task_type, POOLS["default"])
            
            scored_models = []
            for m in pool:
                score = self.calculate_score(m, self.last_task_type)
                if score >= 0:
                    scored_models.append((m, score))
                    
            scored_models.sort(key=lambda x: x[1], reverse=True)
            
            if not scored_models:
                raise Exception("No healthy models available in pool for this task.")
                
            target_models = [m[0] for m in scored_models]
        self.last_pool = target_models
        
        for i, target_model in enumerate(target_models):
            self.load_balancer.increment_load(target_model)
            
            if force_models:
                if i == 0:
                    print(f"[Router] Routing to PRIMARY: {target_model}")
                else:
                    print(f"[Router] Routing to FALLBACK: {target_model}")
            else:
                if i == 0:
                    print(f"[Router] Routing to PRIMARY: {target_model} (Score: {scored_models[i][1]:.2f})")
                else:
                    print(f"[Router] Routing to FALLBACK: {target_model} (Score: {scored_models[i][1]:.2f})")
                
            start_time = TimeTracker.get_current_time()
            try:
                response = LLMClient.completion(
                    model=target_model,
                    messages=messages,
                    **kwargs
                )
                latency_ms = TimeTracker.calculate_latency_ms(start_time)
                self.load_balancer.update_state(target_model, latency_ms, success=True)
                # To communicate to the gateway if it was a fallback
                self.last_selected_model = target_model
                self.last_was_fallback = (i > 0)
                return response
                
            except Exception as e:
                latency_ms = TimeTracker.calculate_latency_ms(start_time)
                self.load_balancer.update_state(target_model, latency_ms, success=False)
                
                # Get a short reason for the fallback
                error_str = str(e).lower()
                if "api key" in error_str or "authentication" in error_str or "401" in error_str:
                    reason = "Invalid API Key"
                elif isinstance(e, (LLMRateLimitError, LLMAPIConnectionError, LLMTimeoutError)):
                    reason = type(e).__name__
                else:
                    reason = str(e).split('\n')[0][:80]
                
                self.last_skipped.append((target_model, reason))
                print(f"[Router Warning] Fallback triggered for {target_model} due to: {reason}")
                continue
                
        raise RuntimeError(f"All models in fallback hierarchy failed for task: {self.last_task_type}")

class GatewayRouter:
    """Wrapper to remain compatible with existing gateway code structure."""
    def __new__(cls):
        return EnterpriseRouter()
