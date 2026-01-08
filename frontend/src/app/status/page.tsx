"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { API_BASE_URL } from "@/lib/constants";

interface EndpointStatus {
  name: string;
  url: string;
  status: "checking" | "up" | "down" | "degraded";
  latency: number | null;
  lastChecked: Date | null;
  details?: string;
}

const ENDPOINTS = [
  { name: "API Health", url: `${API_BASE_URL}/api/health`, key: "health" },
  { name: "Infrastructure", url: `${API_BASE_URL}/api/health/deep`, key: "deep" },
  { name: "Song Catalog", url: `${API_BASE_URL}/api/catalog/songs?q=test&per_page=1`, key: "catalog" },
  { name: "Frontend", url: "https://decide.nomadkaraoke.com", key: "frontend" },
];

export default function StatusPage() {
  const [endpoints, setEndpoints] = useState<EndpointStatus[]>(
    ENDPOINTS.map((e) => ({
      name: e.name,
      url: e.url,
      status: "checking",
      latency: null,
      lastChecked: null,
    }))
  );
  const [lastFullCheck, setLastFullCheck] = useState<Date | null>(null);
  const [isChecking, setIsChecking] = useState(true);

  const checkEndpoint = async (endpoint: typeof ENDPOINTS[0]): Promise<EndpointStatus> => {
    const startTime = performance.now();
    try {
      const response = await fetch(endpoint.url, {
        method: "GET",
        mode: "cors",
        signal: AbortSignal.timeout(30000),
      });
      const latency = Math.round(performance.now() - startTime);

      if (response.ok) {
        // For deep health, check the status field
        if (endpoint.key === "deep") {
          try {
            const data = await response.json();
            if (data.status === "healthy") {
              return {
                name: endpoint.name,
                url: endpoint.url,
                status: "up",
                latency,
                lastChecked: new Date(),
                details: "All services healthy",
              };
            } else {
              return {
                name: endpoint.name,
                url: endpoint.url,
                status: "degraded",
                latency,
                lastChecked: new Date(),
                details: `Status: ${data.status}`,
              };
            }
          } catch {
            return {
              name: endpoint.name,
              url: endpoint.url,
              status: "up",
              latency,
              lastChecked: new Date(),
            };
          }
        }

        return {
          name: endpoint.name,
          url: endpoint.url,
          status: "up",
          latency,
          lastChecked: new Date(),
        };
      } else {
        return {
          name: endpoint.name,
          url: endpoint.url,
          status: "down",
          latency,
          lastChecked: new Date(),
          details: `HTTP ${response.status}`,
        };
      }
    } catch (error) {
      const latency = Math.round(performance.now() - startTime);
      return {
        name: endpoint.name,
        url: endpoint.url,
        status: "down",
        latency,
        lastChecked: new Date(),
        details: error instanceof Error ? error.message : "Connection failed",
      };
    }
  };

  const runAllChecks = useCallback(async () => {
    setIsChecking(true);

    // Check all endpoints in parallel
    const results = await Promise.all(
      ENDPOINTS.map((endpoint) => checkEndpoint(endpoint))
    );

    setEndpoints(results);
    setLastFullCheck(new Date());
    setIsChecking(false);
  }, []);

  useEffect(() => {
    // Data fetching on mount is a valid use case for setState in useEffect
    // eslint-disable-next-line
    runAllChecks();
    // Auto-refresh every 60 seconds
    const interval = setInterval(runAllChecks, 60000);
    return () => clearInterval(interval);
  }, [runAllChecks]);

  const overallStatus = endpoints.every((e) => e.status === "up")
    ? "operational"
    : endpoints.some((e) => e.status === "down")
      ? "outage"
      : endpoints.some((e) => e.status === "checking")
        ? "checking"
        : "degraded";

  const getStatusColor = (status: string) => {
    switch (status) {
      case "up":
      case "operational":
        return "bg-green-500";
      case "degraded":
        return "bg-yellow-500";
      case "down":
      case "outage":
        return "bg-red-500";
      default:
        return "bg-gray-500 animate-pulse";
    }
  };

  const getStatusText = (status: string) => {
    switch (status) {
      case "up":
        return "Operational";
      case "degraded":
        return "Degraded";
      case "down":
        return "Down";
      case "checking":
        return "Checking...";
      default:
        return status;
    }
  };

  return (
    <main className="min-h-screen py-12 px-4">
      <div className="max-w-2xl mx-auto">
        {/* Header */}
        <div className="text-center mb-8">
          <Link href="/" className="text-[var(--brand-pink)] hover:text-[var(--brand-pink)]/80 text-sm mb-4 inline-block">
            &larr; Back to App
          </Link>
          <h1 className="text-3xl font-bold text-[var(--text)] mb-2">System Status</h1>
          <p className="text-[var(--text-muted)]">
            Real-time status of Nomad Karaoke Decide services
          </p>
        </div>

        {/* Overall Status */}
        <div className="bg-[var(--card)] rounded-xl p-6 mb-8 border border-[var(--card-border)]">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className={`w-4 h-4 rounded-full ${getStatusColor(overallStatus)}`} />
              <div>
                <h2 className="text-xl font-semibold text-[var(--text)]">
                  {overallStatus === "operational" && "All Systems Operational"}
                  {overallStatus === "degraded" && "Partial System Outage"}
                  {overallStatus === "outage" && "Major System Outage"}
                  {overallStatus === "checking" && "Checking Systems..."}
                </h2>
                {lastFullCheck && (
                  <p className="text-[var(--text-subtle)] text-sm">
                    Last checked: {lastFullCheck.toLocaleTimeString()}
                  </p>
                )}
              </div>
            </div>
            <button
              onClick={runAllChecks}
              disabled={isChecking}
              className="px-4 py-2 bg-[var(--secondary)] hover:bg-[var(--secondary)] rounded-lg text-[var(--text)] text-sm transition-colors disabled:opacity-50"
            >
              {isChecking ? "Checking..." : "Refresh"}
            </button>
          </div>
        </div>

        {/* Individual Endpoints */}
        <div className="space-y-4">
          <h3 className="text-lg font-medium text-[var(--text)]">Services</h3>
          {endpoints.map((endpoint) => (
            <div
              key={endpoint.name}
              className="bg-[var(--card)] rounded-lg p-4 border border-[var(--card-border)]"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className={`w-3 h-3 rounded-full ${getStatusColor(endpoint.status)}`} />
                  <div>
                    <h4 className="text-[var(--text)] font-medium">{endpoint.name}</h4>
                    {endpoint.details && (
                      <p className="text-[var(--text-subtle)] text-sm">{endpoint.details}</p>
                    )}
                  </div>
                </div>
                <div className="text-right">
                  <p className={`text-sm font-medium ${
                    endpoint.status === "up" ? "text-green-400" :
                    endpoint.status === "degraded" ? "text-yellow-400" :
                    endpoint.status === "down" ? "text-red-400" :
                    "text-[var(--text-subtle)]"
                  }`}>
                    {getStatusText(endpoint.status)}
                  </p>
                  {endpoint.latency !== null && (
                    <p className="text-[var(--text-subtle)] text-xs">{endpoint.latency}ms</p>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Footer */}
        <div className="mt-12 text-center text-[var(--text-subtle)] text-sm">
          <p>
            Automated monitoring runs every 5 minutes via{" "}
            <a
              href="https://github.com/nomadkaraoke/karaoke-decide/actions/workflows/uptime-monitor.yml"
              target="_blank"
              rel="noopener noreferrer"
              className="text-[var(--brand-pink)] hover:underline"
            >
              GitHub Actions
            </a>
          </p>
          <p className="mt-2">
            For real-time 1-minute checks, see{" "}
            <a
              href="https://stats.uptimerobot.com/your-status-page"
              target="_blank"
              rel="noopener noreferrer"
              className="text-[var(--brand-pink)] hover:underline"
            >
              UptimeRobot Status
            </a>
          </p>
        </div>
      </div>
    </main>
  );
}
