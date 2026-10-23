import { Component, inject } from '@angular/core';
import { AgentLensApi } from '../../core/api.service';
import { fmtDateTime, fmtUsd, shortId } from '../../core/format';
import { loadFrom } from '../../core/loadable';
import { StateView } from '../../shared/state-view';

@Component({
  selector: 'app-configs',
  imports: [StateView],
  template: `
    <div class="page">
      <div class="page-head"><div><h1>Configs</h1><p>Immutable agent snapshots. An eval result always traces back to one of these.</p></div></div>
      <div class="card">
        <app-state [status]="data.status()" [error]="data.error()" [empty]="(data.data()?.length ?? 0) === 0" emptyText="No configs yet." (retry)="data.reload()">
          <div class="table-wrap">
            <table>
              <thead><tr><th>Name</th><th>Model</th><th>Prompt</th><th>Tools</th><th class="right">Temp</th><th class="right">Max steps</th><th class="right">Cost cap</th><th>Hash</th><th>Created</th></tr></thead>
              <tbody>
                @for (c of data.data(); track c.id) {
                  <tr>
                    <td><strong>{{ c.name }}</strong></td>
                    <td>{{ c.provider }} · {{ c.model }}</td>
                    <td>{{ c.system_prompt_id }} <span class="muted mono">{{ short(c.system_prompt_sha) }}</span></td>
                    <td>@if ((c.tool_allowlist ?? []).length === 0) { <span class="muted">none</span> } @else { @for (t of c.tool_allowlist; track t) { <span class="chip">{{ t }}</span> {{ ' ' }} } }</td>
                    <td class="right">{{ c.temperature }}</td>
                    <td class="right">{{ c.max_steps }}</td>
                    <td class="right">{{ c.max_cost_usd ? usd(c.max_cost_usd) : '–' }}</td>
                    <td class="mono">{{ short(c.config_hash) }}</td>
                    <td class="nowrap">{{ dt(c.created_at) }}</td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        </app-state>
      </div>
    </div>
  `,
})
export class Configs {
  private readonly api = inject(AgentLensApi);
  protected readonly data = loadFrom(() => 0, () => this.api.listConfigs());
  protected readonly short = shortId;
  protected readonly usd = fmtUsd;
  protected readonly dt = fmtDateTime;
}
