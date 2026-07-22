import {
  Activity,
  AppWindow,
  ArrowLeftRight,
  Bot,
  ChartNoAxesCombined,
  CircleDollarSign,
  Code2,
  Landmark,
  LayoutGrid,
  MonitorSmartphone,
  PlugZap,
  Rocket,
  ShieldCheck,
  Trophy,
  Users,
} from 'lucide-react';
import type { HubNavIconName } from '@/components/hub-nav-config';

const ICONS = {
  workspace: Code2,
  ide: AppWindow,
  remote: MonitorSmartphone,
  connections: PlugZap,
  launch: Rocket,
  capital: Landmark,
  projects: LayoutGrid,
  founders: Users,
  agents: Bot,
  updates: Activity,
  trust: ShieldCheck,
  rankings: Trophy,
  markets: ChartNoAxesCombined,
  swap: ArrowLeftRight,
  predictions: CircleDollarSign,
} satisfies Record<HubNavIconName, typeof Activity>;

export function HubNavIcon({ name, className = 'h-4 w-4' }: { name: HubNavIconName; className?: string }) {
  const Icon = ICONS[name];
  return <Icon className={className} strokeWidth={1.8} aria-hidden />;
}
