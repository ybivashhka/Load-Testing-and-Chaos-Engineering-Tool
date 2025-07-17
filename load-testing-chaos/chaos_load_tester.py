import argparse
import threading
import time
import random
from typing import Dict
import logging

from locust import HttpUser, task, between
from locust.env import Environment
from locust.runners import LocalRunner
from kubernetes import client, config
from kubernetes.client.rest import ApiException
from locust.exception import ResponseError, LocustError  # Для catch

# Настройка logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ServiceUser(HttpUser):
    wait_times = between(5, 10)  # Замедлено для стабильности

    @task
    def get_endpoint(self):
        logger.info("Performing GET request to /")
        try:
            self.client.get("/", timeout=2)  # Timeout to prevent hang
        except (ResponseError, LocustError, TimeoutError, Exception) as e:
            logger.error(f"Request error: {str(e)}")  # Catch all possible

def inject_chaos(namespace: str, probability: float = 0.1, interval: int = 10):
    try:
        config.load_kube_config()
        v1 = client.CoreV1Api()
    except Exception as e:
        logger.error(f"Kubernetes config error: {e}")
        return

    while True:
        if random.random() < probability:
            try:
                pods = v1.list_namespaced_pod(namespace)
                if pods.items:
                    pod = random.choice(pods.items)
                    v1.delete_namespaced_pod(pod.metadata.name, namespace)
                    logger.info(f"Chaos: Deleted pod '{pod.metadata.name}'")
                else:
                    logger.warning(f"No pods in '{namespace}'")
            except ApiException as e:
                logger.error(f"Kubernetes API error: {e}")
        time.sleep(interval)

def generate_report(env: Environment, slo: float = 0.999) -> Dict[str, any]:
    stats = env.stats  # Correct access
    total_requests = stats.total.num_requests
    failures = stats.total.num_failures

    if total_requests == 0:
        logger.warning("No requests sent - check logs for errors or connection issues")
        return {
            "total_requests": 0,
            "failures": 0,
            "availability_percent": 0.0,
            "average_latency_ms": 0.0,
            "error_budget_consumed_percent": "N/A (no data)"
        }
    
    availability = (1 - (failures / total_requests)) * 100
    error_rate = 1 - (availability / 100)
    allowed_error_rate = 1 - slo
    error_budget_consumed = (error_rate / allowed_error_rate) * 100 if allowed_error_rate > 0 and error_rate > 0 else 0.0
    
    avg_latency = stats.total.avg_response_time if total_requests > 0 else 0.0
    
    # Manual debug print if needed
    # print(f"Debug: Total requests from stats: {total_requests}, Failures: {failures}, Avg latency: {avg_latency}")

    return {
        "total_requests": total_requests,
        "failures": failures,
        "availability_percent": availability,
        "average_latency_ms": avg_latency,
        "error_budget_consumed_percent": error_budget_consumed
    }

def main(args):
    env = Environment(user_classes=[ServiceUser])
    env.host = args.url
    runner = env.create_local_runner()

    # Reset stats
    env.stats.reset_all()

    if args.chaos_prob > 0:
        chaos_thread = threading.Thread(target=inject_chaos, args=(args.namespace, args.chaos_prob), daemon=True)
        chaos_thread.start()
        logger.info(f"Chaos enabled with prob {args.chaos_prob}")

    logger.info(f"Starting test: {args.users} users for {args.duration}s on {args.url}")
    runner.start(args.users, spawn_rate=1)
    time.sleep(args.duration)
    runner.quit()
    logger.info("Test completed - check server logs for GET requests")

    report = generate_report(env, args.slo)
    print("\n--- Отчёт по нагрузочному тесту и хаосу ---")
    print(f"Всего запросов: {report['total_requests']}")
    print(f"Сбоев: {report['failures']}")
    print(f"Доступность: {report['availability_percent']:.2f}%")
    print(f"Средняя задержка: {report['average_latency_ms']:.2f} мс")
    print(f"Потребление error budget: {report['error_budget_consumed_percent']} (SLO: {args.slo * 100}%)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SRE инструмент для тестов")
    parser.add_argument("--url", required=True, help="URL сервиса")
    parser.add_argument("--users", type=int, default=1, help="Пользователи")
    parser.add_argument("--duration", type=int, default=5, help="Длительность сек")
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--chaos-prob", type=float, default=0.1)
    parser.add_argument("--slo", type=float, default=0.999)
    args = parser.parse_args()
    main(args)