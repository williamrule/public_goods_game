from operator import truediv




from otree.api import *
import random
import math




doc = """
Treatment 1: Exogenous firms + internal constant returns (linear public good).
"""




class C(BaseConstants):
   NAME_IN_URL = 'pg_exogenous'
   PLAYERS_PER_GROUP = None
   NUM_ROUNDS = 30


   ENDOWMENT = 8


   # Table 2: MCPR alpha by firm size n (index = n)
   MPCR_BY_SIZE = {2: 0.65, 3: 0.55, 4: 0.49, 5: 0.45, 6: 0.42}


   # Timing per period to allocate effort between firm and themselves
   DECISION_SECONDS = 60
   # Timing for information relay sub-period
   INFO_SECONDS = 30


   # Exogenous block structure
   BLOCK_LENGTH = 10  # Number of rounds before reshuffle (30 rounds total)
   EXO_SIZES = [2, 3, 4, 5, 6]  # Sums to 20




def current_block_start(round_number: int) -> int:
   if round_number <= 10:
       return 1
   elif round_number <= 20:
       return 11
   else:
       return 21




# Helper function to build valid groups
def build_exogenous_matrix(players, sizes, current_round, max_tries=5000):
   sizes_order = sorted(sizes, reverse=True)


   for _ in range(max_tries):
       remaining = players.copy()
       random.shuffle(remaining)
       chosen_groups = []
       ok = True


       for size in sizes_order:
           eligible = []
           for p in remaining:
               size_by_block = p.participant.vars.get('size_by_block', {})
               # only sizes from earlier blocks (keys < current_round)
               past_sizes = [v for k, v in size_by_block.items()
                             if k < current_round]
               if size not in past_sizes:
                   eligible.append(p)


           if len(eligible) < size:
               ok = False
               break


           members = random.sample(eligible, size)
           chosen_groups.append((size, members))
           for p in members:
               remaining.remove(p)


       if ok and not remaining:
           size_to_members = {s: m for s, m in chosen_groups}
           return [size_to_members[s] for s in sizes]


   raise Exception(
       "Could not find a valid grouping without repeated firm sizes.")




class Subsession(BaseSubsession):
   pass




def creating_session(subsession: Subsession):
   players = subsession.get_players()
   test_mode = subsession.session.config.get('test_mode', False)


   if subsession.round_number in [1, 11, 21]:


       if (not test_mode) and len(players) != sum(C.EXO_SIZES):
           raise Exception(
               f'T1 requires exactly 20 participants; currently {len(players)}'
           )


       if test_mode:
           # simple grouping for testing: groups of 2, last group may be smaller
           random.shuffle(players)
           matrix = []
           i = 0
           while i < len(players):
               matrix.append(players[i:i + 2])
               i += 2
           subsession.set_group_matrix(matrix)
       else:
           matrix = build_exogenous_matrix(
               players, C.EXO_SIZES, subsession.round_number)
           subsession.set_group_matrix(matrix)


           # record each participant's size assignment for this block start
           for p in subsession.get_players():
               current_size = len(p.group.get_players())
               size_by_block = dict(
                   p.participant.vars.get('size_by_block', {}))
               size_by_block[subsession.round_number] = current_size
               p.participant.vars['size_by_block'] = size_by_block


       # Assign stable Firm IDs for this 10-round block (Firm 1..Firm K)
       block_start = subsession.round_number
       groups = subsession.get_groups()
       for firm_label, g in enumerate(groups, start=1):
           for p in g.get_players():
               firm_by_block = dict(
                   p.participant.vars.get('firm_by_block', {}))
               firm_by_block[block_start] = firm_label
               p.participant.vars['firm_by_block'] = firm_by_block


   else:
       if subsession.round_number <= 10:
           subsession.group_like_round(1)
       elif subsession.round_number <= 20:
           subsession.group_like_round(11)
       else:
           subsession.group_like_round(21)




class Group(BaseGroup):
   total_effort = models.FloatField(initial=0)
   firm_size = models.IntegerField(initial=0)
   per_capita_effort = models.FloatField(initial=0)
   per_capita_payout = models.FloatField(initial=0)




class Player(BasePlayer):
   effort_to_firm = models.FloatField(
       min=0,
       max=C.ENDOWMENT,
       label="How many units of effort do you allocate to your firm?"
   )
   payoff_points = models.FloatField(initial=0)




def total_points_so_far(player: Player) -> float:
   # sum of payoffs from completed rounds (excludes current round)
   total = 0
   for r in range(1, player.round_number):
       total += player.in_round(r).payoff
   return total




def set_payoffs(group: Group):
   players = group.get_players()
   n = len(players)
   group.firm_size = n


   total_effort = sum(p.effort_to_firm for p in players)
   group.total_effort = total_effort


   group.per_capita_effort = total_effort / n if n else 0


   # STRICT: avoids accidental wrong treatment
   returns_type = group.session.config['returns_type']


   if returns_type == 'constant':
       # prefer dict instead of list for safety
       if n not in C.MPCR_BY_SIZE:
           raise Exception(
               f"No MPCR specified for firm size n={n}. Check C.MPCR_BY_SIZE.")
       alpha = C.MPCR_BY_SIZE[n]
       per_capita_payout = alpha * total_effort


   elif returns_type == 'increasing':
       a = float(group.session.config['a'])
       b = float(group.session.config['b'])
       output = a * (total_effort ** b) if total_effort > 0 else 0.0
       per_capita_payout = output / n


   else:
       raise Exception(f"Unknown returns_type: {returns_type}")


   group.per_capita_payout = per_capita_payout


   for p in players:
       p.payoff = (C.ENDOWMENT - p.effort_to_firm) + per_capita_payout




def set_payoffs_all_groups(subsession: Subsession):
   for g in subsession.get_groups():
       set_payoffs(g)




def total_points_so_far(player: Player) -> float:
   total = 0
   for r in range(1, player.round_number):
       total += player.in_round(r).payoff
   return total




class ResultsWaitPage(WaitPage):
   wait_for_all_groups = True
   template_name = 'pg_exogenous/ResultsWaitPage.html'
   after_all_players_arrive = set_payoffs_all_groups


   @staticmethod
   def vars_for_template(player: Player):
       effort_to_firm = float(player.effort_to_firm or 0.0)
       effort_to_firm = round(effort_to_firm, 2)


       effort_kept = round(C.ENDOWMENT - effort_to_firm, 2)


       return dict(
           effort_kept=effort_kept,  # numeric (if you ever need it)
           effort_to_firm_disp=f"{effort_to_firm:.2f}",
           effort_kept_disp=f"{effort_kept:.2f}",
           total_points_so_far=total_points_so_far(player),
       )




# PAGES
class Tutorial(Page):
   @staticmethod
   def is_displayed(player: Player):
       return player.round_number == 1




class Decision(Page):
   form_model = 'player'
   form_fields = ['effort_to_firm']
   timeout_seconds = C.DECISION_SECONDS
   timeout_submission = {'effort_to_firm': 0.0}


   @staticmethod
   def vars_for_template(player: Player):
       return dict(
           total_points_so_far=total_points_so_far(player),
       )


   @staticmethod
   def before_next_page(player: Player, timeout_happened):
       # Enforce 2 decimals (and avoid float artifacts like 7.999999)
       player.effort_to_firm = round(float(player.effort_to_firm or 0.0), 2)


   @staticmethod
   def error_message(player: Player, values):
       # Optional but recommended: enforce 0.01 grid even if someone hacks the HTML
       x = values.get('effort_to_firm')
       if x is None:
           return
       if abs(x - round(x, 2)) > 1e-9:
           return "Please choose effort in increments of 0.01."




class Results(Page):
   timeout_seconds = 30


   @staticmethod
   def vars_for_template(player: Player):
       group = player.group


       # Always work with rounded effort to avoid float artifacts
       effort_to_firm = round(float(player.effort_to_firm or 0.0), 2)
       effort_kept = round(C.ENDOWMENT - effort_to_firm, 2)


       total_firm_effort = float(group.total_effort or 0.0)
       firm_size = group.firm_size
       per_capita_payout = float(group.per_capita_payout or 0.0)


       total_payoff = float(player.payoff or 0.0)
       selfish_payoff = effort_kept
       payoff_if_zero = C.ENDOWMENT + per_capita_payout
       personal_cost = payoff_if_zero - total_payoff


       # Display strings (2 decimals everywhere)
       def fmt2(x):
           return f"{float(x or 0.0):.2f}"


       return dict(
           effort_to_firm=effort_to_firm,
           effort_kept=effort_kept,
           selfish_payoff=selfish_payoff,
           total_firm_effort=total_firm_effort,
           firm_size=firm_size,
           per_capita_payout=per_capita_payout,
           total_payoff=total_payoff,
           personal_cost=personal_cost,
           total_points_so_far=total_points_so_far(player),


           # formatted versions for templates
           effort_to_firm_disp=fmt2(effort_to_firm),
           effort_kept_disp=fmt2(effort_kept),
           selfish_payoff_disp=fmt2(selfish_payoff),
           total_firm_effort_disp=fmt2(total_firm_effort),
           per_capita_payout_disp=fmt2(per_capita_payout),
           total_payoff_disp=fmt2(total_payoff),
           personal_cost_disp=fmt2(personal_cost),
       )




class Relay(Page):
   timeout_seconds = C.INFO_SECONDS


   @staticmethod
   def vars_for_template(player: Player):
       block_start = current_block_start(player.round_number)


       def firm_label_for_group(g):
           any_member = g.get_players()[0]
           return any_member.participant.vars.get('firm_by_block', {}).get(block_start, None)


       rows = []
       for g in player.subsession.get_groups():
           rows.append(dict(
               firm_id=firm_label_for_group(g),
               firm_size=len(g.get_players()),
               per_capita_effort=g.per_capita_effort,
               per_capita_payout=g.per_capita_payout,




               # ✅ display versions (strings)
               per_capita_effort_disp=f"{g.per_capita_effort:.1f}",
               per_capita_payout_disp=f"{g.per_capita_payout:.2f}",
           ))


       rows.sort(key=lambda r: r["firm_id"]
                 if r["firm_id"] is not None else 999)


       my_firm_id = player.participant.vars.get(
           'firm_by_block', {}).get(block_start, None)


       return dict(
           rows=rows,
           my_firm_id=my_firm_id,
           total_points_so_far=total_points_so_far(player),
       )




class FinalSummary(Page):
   @staticmethod
   def is_displayed(player: Player):
       return player.round_number == C.NUM_ROUNDS


   @staticmethod
   def vars_for_template(player: Player):
       total_points = sum(float(p.payoff) for p in player.in_all_rounds())


       payout_per_point = player.session.config.get('payout_per_point', 0.09)
       participation_fee = player.session.config.get('participation_fee', 0)


       earnings_dollars = total_points * payout_per_point
       total_payment = earnings_dollars + participation_fee


       return dict(
           total_points=f"{total_points:.2f}",
           payout_per_point=f"{payout_per_point:.2f}",
           earnings_dollars=f"{earnings_dollars:.2f}",
           participation_fee=f"{participation_fee:.2f}",
           total_payment=f"{total_payment:.2f}",
       )




page_sequence = [Tutorial, Decision,
                ResultsWaitPage, Results, Relay, FinalSummary]



