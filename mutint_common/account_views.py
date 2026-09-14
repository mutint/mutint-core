"""The account page Django does not ship: changing your own email address.

Django's auth views cover signing in, signing out and changing a password, and nothing lets a
signed-in person set the address on their own `User` row -- that field is written by Django
admin, or by `start.py` creating the first admin with a placeholder. It matters now because a
component sends it somewhere: mutint-refsniff's BLAST searches carry the person's address to
NCBI, which asks that every automated search name someone to contact, and a placeholder or a
blank is the wrong thing to send on their behalf.

Function-based and hand-written, like every page here. Signed-in only, redirecting to the
login page the way `PasswordChangeView` does, so the two account pages behave alike.
"""

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.shortcuts import render
from django.urls import reverse

from mutint_common.util import get_user_context


def email_change(request):
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path(), reverse("accounts:login"))

    context = get_user_context(request.user)
    context.update({"email": request.user.email, "saved": False, "error": ""})

    if request.method == "POST":
        email = (request.POST.get("email") or "").strip()
        context["email"] = email
        try:
            if not email:
                raise ValidationError("Enter an email address.")
            validate_email(email)
        except ValidationError as refused:
            context["error"] = " ".join(refused.messages)
        else:
            if email != request.user.email:
                request.user.email = email
                request.user.save(update_fields=["email"])
            context["saved"] = True

    return render(request, "accounts/email_change.html", context)
