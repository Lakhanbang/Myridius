"""
- `ProviderState`: A data class representing latency, error rate, success rate, and health status (Healthy, Degraded, Unhealthy) for a model.
- `AdaptiveLoadBalancer`: Keeps track of state across multiple providers.
- Dynamically updates the success/failure history and recalculates exponential moving averages for latency after each request.
"""

class ProviderState:
    """Tracks the health and performance metrics of a specific LLM provider in real-time."""
    def __init__(self):
        self.latency_ms = 1000.0  # Initial assumed latency
        self.success_count = 10   # Initial smooth factor
        self.error_count = 0
        self.current_load = 0
        self.status = "Healthy"   # Healthy, Degraded, Unhealthy
    
    @property
    def error_rate(self) -> float:
        total = self.success_count + self.error_count
        if total == 0: return 0.0
        return self.error_count / total
        
    @property
    def success_rate(self) -> float:
        return 1.0 - self.error_rate


class AdaptiveLoadBalancer:
    """
    Manages state, tracks performance, and provides adaptive health awareness for LLM models.
    """
    def __init__(self, models: list[str]):
        self.states = {m: ProviderState() for m in models}

    def get_state(self, model: str) -> ProviderState:
        """Retrieves the current state of a model."""
        return self.states[model]

    def increment_load(self, model: str):
        """Increases the active request count for a model."""
        self.states[model].current_load += 1

    def decrement_load(self, model: str):
        """Decreases the active request count for a model."""
        state = self.states[model]
        state.current_load = max(0, state.current_load - 1)

    def update_state(self, model: str, latency_ms: float, success: bool):
        """
        Updates the Provider State organically to drive Adaptive Weighted Load Balancing.
        Adjusts latency averages and handles health degradation/recovery.
        """
        self.decrement_load(model)
        state = self.states[model]
        
        if success:
            # Exponential moving average for latency
            state.latency_ms = (state.latency_ms * 0.8) + (latency_ms * 0.2)
            state.success_count += 1
            # Recovery mechanism
            if state.status == "Degraded" and state.success_rate > 0.9:
                state.status = "Healthy"
        else:
            state.error_count += 1
            error_rate = state.error_rate
            # Status degradation mechanism
            if error_rate > 0.5:
                state.status = "Unhealthy"
            elif error_rate > 0.1:
                state.status = "Degraded"
