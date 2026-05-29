export interface JwtPayload {
  sub: string;
  email: string;
  role: string;
}

export interface AuthUser {
  id: string;
  email: string;
  name: string | null;
  role: string;
  reputationPoints: number;
  contributorLevel: number;
}

export interface AuthResponse {
  accessToken?: string;
  user?: AuthUser;
  requires2fa?: boolean;
  pendingToken?: string;
  methods?: string[];
}
