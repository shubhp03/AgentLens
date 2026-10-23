import { inject } from '@angular/core';
import { CanActivateFn, Router, Routes } from '@angular/router';
import { AuthService } from './core/auth.service';

const authGuard: CanActivateFn = (_route, state) => {
  const auth = inject(AuthService);
  return auth.isAuthed() || inject(Router).createUrlTree(['/login'], { queryParams: { returnUrl: state.url } });
};

export const routes: Routes = [
  { path: 'login', title: 'Sign in · AgentLens', loadComponent: () => import('./features/login/login').then((m) => m.Login) },
  {
    path: '',
    canActivate: [authGuard],
    children: [
      { path: '', pathMatch: 'full', title: 'Overview · AgentLens', loadComponent: () => import('./features/overview/overview').then((m) => m.Overview) },
      { path: 'runs', title: 'Runs · AgentLens', loadComponent: () => import('./features/runs/runs').then((m) => m.Runs) },
      { path: 'runs/:id', title: 'Run · AgentLens', loadComponent: () => import('./features/run-detail/run-detail').then((m) => m.RunDetailPage) },
      { path: 'ask', title: 'Ask · AgentLens', loadComponent: () => import('./features/playground/playground').then((m) => m.Playground) },
      { path: 'evals', title: 'Evals · AgentLens', loadComponent: () => import('./features/evals/evals').then((m) => m.Evals) },
      // Literal path must come before ':id'.
      { path: 'evals/compare', title: 'Compare · AgentLens', loadComponent: () => import('./features/compare/compare').then((m) => m.Compare) },
      { path: 'evals/:id', title: 'Eval run · AgentLens', loadComponent: () => import('./features/eval-detail/eval-detail').then((m) => m.EvalDetail) },
      { path: 'configs', title: 'Configs · AgentLens', loadComponent: () => import('./features/configs/configs').then((m) => m.Configs) },
    ],
  },
  { path: '**', redirectTo: '' },
];
