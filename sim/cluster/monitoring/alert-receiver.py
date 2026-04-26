#!/usr/bin/env python3
"""
Alert receiver webhook service.
Receives alerts from Alertmanager and exposes them via /alerts endpoint.
"""

import json
import logging
from datetime import datetime
from typing import List, Dict, Any
from flask import Flask, request, jsonify
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# In-memory alert store (in production, use a database)
alerts_store: Dict[str, Any] = {
    "current_alerts": [],
    "historical_alerts": [],
}

ALERTS_FILE = Path("/tmp/netpilot-alerts.jsonl")


def save_alert(alert_group: Dict[str, Any]) -> None:
    """Save alert to JSONL file."""
    for alert in alert_group.get("alerts", []):
        alert_record = {
            "timestamp": datetime.utcnow().isoformat(),
            "status": alert.get("status"),
            "labels": alert.get("labels", {}),
            "annotations": alert.get("annotations", {}),
            "startsAt": alert.get("startsAt"),
            "endsAt": alert.get("endsAt"),
        }
        with open(ALERTS_FILE, "a") as f:
            f.write(json.dumps(alert_record) + "\n")
        logger.info(f"Alert saved: {alert_record}")


@app.route("/webhook", methods=["POST"])
def receive_alerts() -> tuple:
    """Receive webhook alerts from Alertmanager."""
    try:
        data = request.get_json()
        
        if not data:
            logger.warning("Received empty webhook payload")
            return jsonify({"status": "error", "message": "Empty payload"}), 400
        
        logger.info(f"Received webhook: {json.dumps(data, indent=2)}")
        
        # Extract alerts from the webhook payload
        alerts = data.get("alerts", [])
        group_labels = data.get("groupLabels", {})
        common_labels = data.get("commonLabels", {})
        common_annotations = data.get("commonAnnotations", {})
        
        # Process each alert
        for alert in alerts:
            alert_item = {
                "status": alert.get("status"),
                "labels": {**common_labels, **alert.get("labels", {})},
                "annotations": {**common_annotations, **alert.get("annotations", {})},
                "startsAt": alert.get("startsAt"),
                "endsAt": alert.get("endsAt"),
                "received_at": datetime.utcnow().isoformat(),
            }
            
            # Update current alerts
            alert_key = f"{alert_item['labels'].get('alertname')}_{alert_item['labels'].get('service', 'global')}"
            
            if alert_item["status"] == "firing":
                # Add or update firing alert
                existing = next(
                    (a for a in alerts_store["current_alerts"] if a.get("key") == alert_key),
                    None,
                )
                if existing:
                    alerts_store["current_alerts"].remove(existing)
                alert_item["key"] = alert_key
                alerts_store["current_alerts"].append(alert_item)
                logger.info(f"Alert firing: {alert_key}")
            else:
                # Remove resolved alert
                alerts_store["current_alerts"] = [
                    a for a in alerts_store["current_alerts"] if a.get("key") != alert_key
                ]
                logger.info(f"Alert resolved: {alert_key}")
            
            # Store in historical alerts
            alerts_store["historical_alerts"].append(alert_item)
            
            # Save to file
            save_alert({"alerts": [alert]})
        
        return jsonify({"status": "success", "alerts_received": len(alerts)}), 200
    
    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/alerts", methods=["GET"])
def get_alerts() -> Dict[str, Any]:
    """Get current active alerts."""
    return jsonify({
        "timestamp": datetime.utcnow().isoformat(),
        "active_alerts": alerts_store["current_alerts"],
        "alert_count": len(alerts_store["current_alerts"]),
        "total_received": len(alerts_store["historical_alerts"]),
    })


@app.route("/alerts/history", methods=["GET"])
def get_alerts_history() -> Dict[str, Any]:
    """Get all historical alerts."""
    limit = request.args.get("limit", default=100, type=int)
    return jsonify({
        "timestamp": datetime.utcnow().isoformat(),
        "alerts": alerts_store["historical_alerts"][-limit:],
        "total": len(alerts_store["historical_alerts"]),
    })


@app.route("/alerts/active", methods=["GET"])
def get_active_alerts() -> Dict[str, Any]:
    """Get only currently active/firing alerts."""
    active = [a for a in alerts_store["current_alerts"] if a.get("status") == "firing"]
    return jsonify({
        "timestamp": datetime.utcnow().isoformat(),
        "active_alerts": active,
        "count": len(active),
    })


@app.route("/health", methods=["GET"])
def health() -> Dict[str, str]:
    """Health check endpoint."""
    return jsonify({"status": "ok", "service": "alert-receiver"})


@app.route("/", methods=["GET"])
def root() -> Dict[str, str]:
    """Root endpoint."""
    return jsonify({
        "service": "netpilot-alert-receiver",
        "endpoints": {
            "/webhook": "POST - Receive alerts from Alertmanager",
            "/alerts": "GET - Get current active alerts",
            "/alerts/active": "GET - Get only firing alerts",
            "/alerts/history": "GET - Get all historical alerts",
            "/health": "GET - Health check",
        },
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
