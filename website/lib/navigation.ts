export type NavItem = {
  slug: string;
  title: string;
  description: string;
  keywords: string[];
};

export type NavGroup = {
  title: string;
  items: NavItem[];
};

export const navGroups: NavGroup[] = [
  {
    title: "Get started",
    items: [
      {
        slug: "overview",
        title: "What is JenAI?",
        description: "Product scope, supported reference platform, and decision boundary.",
        keywords: ["agent", "ROS 2", "UGV", "brain", "high-level"],
      },
      {
        slug: "isaac-sim-quickstart",
        title: "Isaac Sim QuickStart",
        description: "Run the fixed Nova Carter workflow from Play to a verified task.",
        keywords: ["Isaac Sim", "Nav2", "Carter", "Jazzy", "start"],
      },
    ],
  },
  {
    title: "Core concepts",
    items: [
      {
        slug: "decision-boundary",
        title: "Decision boundary",
        description: "What the agent may decide and what remains in robot controllers.",
        keywords: ["safety", "boundary", "API", "controller"],
      },
      {
        slug: "capability-card",
        title: "Robot Capability Card",
        description: "Authoritative capability, maturity, evidence, and limitation claims.",
        keywords: ["self introduction", "capability", "robot identity"],
      },
      {
        slug: "task-outcomes",
        title: "Task outcomes",
        description: "The honest result contract used by the TUI, reports, and experiments.",
        keywords: ["succeeded", "unverified", "failed", "receipt"],
      },
      {
        slug: "site-profiles",
        title: "Site Profiles",
        description: "Bind map identity, locations, routes, docks, and evidence to one site.",
        keywords: ["map hash", "location", "site", "SHA-256"],
      },
    ],
  },
  {
    title: "Workflows",
    items: [
      {
        slug: "inspect-robot",
        title: "Inspect robot state",
        description: "Read pose, laser, and Nav2 readiness without moving the robot.",
        keywords: ["doctor", "pose", "scan", "read only"],
      },
      {
        slug: "navigate",
        title: "Navigate to a location",
        description: "Save a location, approve motion, and verify the terminal pose.",
        keywords: ["route", "natural language", "goal", "location"],
      },
      {
        slug: "explore",
        title: "Explore known locations",
        description: "Run bounded low-repeat patrols over registered locations.",
        keywords: ["explore", "patrol", "random", "known locations"],
      },
      {
        slug: "dock-approach",
        title: "Dock approach",
        description: "Reach a registered dock pose without making a false charging claim.",
        keywords: ["dock", "charge", "arrived unverified"],
      },
    ],
  },
  {
    title: "Reference",
    items: [
      {
        slug: "commands",
        title: "Command reference",
        description: "Natural language, slash shortcuts, approvals, stop, and reports.",
        keywords: ["TUI", "slash", "help", "stop"],
      },
      {
        slug: "configuration",
        title: "Configuration",
        description: "Vehicle, provider, tolerances, domains, and active site settings.",
        keywords: ["config.toml", "Ollama", "ROS_DOMAIN_ID"],
      },
      {
        slug: "acceptance",
        title: "Validation & acceptance",
        description: "Evidence gates for repeatable simulation and product releases.",
        keywords: ["HIL", "test", "evidence", "release gate"],
      },
      {
        slug: "troubleshooting",
        title: "Troubleshooting",
        description: "Diagnose maps, localization, scans, Nav2, models, and slow commands.",
        keywords: ["warn", "fail", "latency", "AMCL"],
      },
      {
        slug: "future-work",
        title: "Future work",
        description: "Planned capabilities that are intentionally not claimed today.",
        keywords: ["quadruped", "charging", "VLM", "onboarding"],
      },
    ],
  },
];

export const allNavItems = navGroups.flatMap((group) => group.items);
