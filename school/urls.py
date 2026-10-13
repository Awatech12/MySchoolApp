from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('register', views.register, name='register'),
    path('register/complete', views.register_with_picture, name='register_with_picture'),
    path('home', views.home, name='home'),
    path('logout', views.user_logout, name='logout'),



    path('open_notify/<int:pk>', views.open_notify, name='open_notify'),
    path('notify', views.notifications, name='notifications'),
    path('follow/<str:username>', views.follow, name='follow'),
    path('?/<str:username>', views.update_profile, name='update_profile'),

    # ── CBT Exam ──────────────────────────────────────────────────
    path('cbt/',                    views.cbt_subjects,       name='cbt_exams'),       # subject picker
    path('cbt/mathematics/',        views.cbt_mathematics,           name='cbt_mathematics'),
    path('cbt/mathematics/topics/', views.cbt_mathematics_topics, name='cbt_mathematics_topics'),  # ← NEW topic selector
    path('cbt/physics/',            views.cbt_physics,        name='cbt_physics'),     # ← OLD physics CBT (kept)
    path('cbt/physics/topics/',     views.cbt_physics_topics, name='cbt_physics_topics'),  # ← NEW topic selector
    path('cbt/english/',            views.cbt_english,        name='cbt_english'),
    path('cbt/chemistry/',          views.cbt_chemistry,      name='cbt_chemistry'),
    path('cbt/submit/',             views.cbt_submit,         name='cbt_submit'),      # score submission
    # ─────────────────────────────────────────────────────────────

    # Live video URLs
    path('live/', views.live_room_list, name='live_room_list'),
    path('live/create/', views.create_live_room, name='create_live_room'),
    path('live/<str:room_name>/', views.live_room, name='live_room'),
    path('live/<str:room_name>/end/', views.end_live_room, name='end_live_room'),
    path('live/<str:room_name>/start/', views.start_live_room, name='start_live_room'),

    # ── Chat Room / Class Discussion URLs ─────────────────────────
    path('chat/', views.chat_room, name='chat_room'),
    path('chat/create/', views.create_class_post, name='create_class_post'),
    path('chat/post/<uuid:post_id>/', views.chat_post_detail, name='chat_post_detail'),
    path('chat/post/<uuid:post_id>/comment/', views.chat_post_comment, name='chat_post_comment'),
    path('chat/post/<uuid:post_id>/like/', views.chat_post_like, name='chat_post_like'),
    path('chat/comment/<uuid:comment_id>/like/', views.chat_comment_like, name='chat_comment_like'),
    path('chat/post/<uuid:post_id>/resolve/', views.resolve_post, name='resolve_post'),

    # ── Admin dashboard (staff only) ──
    path('dashboard/', views.dashboard_home, name='admin_dashboard'),
    path('dashboard/api/students/', views.api_students, name='dash_students'),
    path('dashboard/api/students/create/', views.api_student_create, name='dash_student_create'),
    path('dashboard/api/students/bulk/', views.api_students_bulk, name='dash_students_bulk'),
    path('dashboard/api/students/import/', views.api_students_import, name='dash_students_import'),
    path('dashboard/api/students/export/', views.api_students_export, name='dash_students_export'),
    path('dashboard/api/students/<int:pk>/update/', views.api_student_update, name='dash_student_update'),
    path('dashboard/api/students/<int:pk>/delete/', views.api_student_delete, name='dash_student_delete'),
    path('dashboard/api/students/<int:pk>/toggle/', views.api_student_toggle, name='dash_student_toggle'),
    path('dashboard/api/students/<int:pk>/reset-password/', views.api_student_reset_password, name='dash_student_reset'),
    path('dashboard/api/students/<int:pk>/results/', views.api_student_results, name='dash_student_results'),
    path('dashboard/api/students/<int:pk>/clear-results/', views.api_student_clear_results, name='dash_student_clear'),
    path('dashboard/api/exams/<uuid:exam_id>/delete/', views.api_exam_delete, name='dash_exam_delete'),
    path('dashboard/api/content/', views.api_content, name='dash_content'),
    path('dashboard/api/content/delete/', views.api_content_delete, name='dash_content_delete'),

    # profile must stay LAST – catches any remaining <str:username>
    path('edit-profile/', views.edit_profile, name='awa_edit_profile'),
    path('subjects/update/', views.update_subjects, name='update_subjects'),
    path('results/print/<str:username>/', views.print_test_results, name='print_test_results'),
    path('<str:username>', views.profile, name='profile'),
]



