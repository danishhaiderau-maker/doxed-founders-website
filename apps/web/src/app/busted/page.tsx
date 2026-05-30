import { redirect } from 'next/navigation';

export default function BustedTradersPage() {
  redirect('/leaderboard?tab=losers');
}
